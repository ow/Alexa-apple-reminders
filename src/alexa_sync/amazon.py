"""Alexa shopping list side of the sync.

Authentication piggybacks on aioamazondevices (the library behind Home
Assistant's Alexa integration): it registers a virtual Alexa-app device once,
which yields a long-lived refresh token. From then on we only ever trade that
token for fresh website cookies; the password and OTP are never stored.

The list endpoints are the same undocumented /alexashoppinglists/api/v2 calls
the library and pyalexatodo use. We call them ourselves so we can paginate and
see every field, but go through the library's HTTP wrapper for cookies/CSRF.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from http import HTTPMethod
from typing import Any

from aiohttp import ClientSession
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.exceptions import CannotAuthenticate
from aioamazondevices.structures import AmazonSaveDataConfig
from yarl import URL

from .sync import Item, normalize

log = logging.getLogger(__name__)

LISTS_PATH = "alexashoppinglists/api/v2/lists"
PAGE_SIZE = 100  # API maximum


class AuthExpired(Exception):
    """The stored device registration no longer works; run `alexa-sync login`."""


def make_api(session: ClientSession, email: str, password: str, login_data: dict | None, data_dir: str) -> AmazonEchoApi:
    return AmazonEchoApi(
        client_session=session,
        login_email=email,
        login_password=password,
        login_data=login_data,
        save_data=AmazonSaveDataConfig(path=data_dir),
    )


class AlexaClient:
    """Async client for the shopping list. Refreshes cookies once on auth failure."""

    def __init__(self, api: AmazonEchoApi) -> None:
        self.api = api
        self.login_data_changed = False

    @property
    def login_data(self) -> dict[str, Any]:
        return self.api.login._session_state_data.login_stored_data

    async def connect(self) -> None:
        await self._with_refresh(self.api.login.login_mode_stored_data)

    async def _refresh_cookies(self) -> None:
        log.info("session cookies rejected; exchanging refresh token for new ones")
        try:
            await self.api.login._refresh_auth_cookies()
        except Exception as exc:
            raise AuthExpired("could not refresh Amazon cookies") from exc
        self.login_data_changed = True

    async def _with_refresh(self, fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        try:
            return await fn()
        except (CannotAuthenticate, ValueError) as exc:
            # ValueError: we got HTML (a sign-in page) instead of JSON.
            log.debug("request failed with %r", exc)
        await self._refresh_cookies()
        try:
            return await fn()
        except (CannotAuthenticate, ValueError) as exc:
            raise AuthExpired("Amazon rejected freshly refreshed cookies") from exc

    async def _call(self, method: HTTPMethod, path: str, query: dict | None = None, body: dict | None = None) -> dict:
        wrapper = self.api._http_wrapper
        url = URL.joinpath(self.api.login._session_state_data.retail_site_url, LISTS_PATH, path)
        if query:
            url = url.with_query(query)

        async def go() -> dict:
            _, resp = await wrapper.session_request(method=method, url=url, input_data=body or {}, json_data=True)
            return await wrapper.response_to_json(resp, "")

        return await self._with_refresh(go)

    async def shopping_list_id(self) -> str:
        data = await self._call(HTTPMethod.POST, "fetch")
        lists = data["listInfoList"]
        shop = [l for l in lists if l.get("listType") == "SHOP"]
        if not shop:
            raise RuntimeError(f"no SHOP list among {[l.get('listType') for l in lists]}")
        return (next((l for l in shop if l.get("defaultList")), shop[0]))["listId"]

    async def items(self, list_id: str) -> list[dict]:
        out: list[dict] = []
        token = None
        while True:
            data = await self._call(
                HTTPMethod.POST, f"{list_id}/items/fetch", {"limit": PAGE_SIZE}, {"nextToken": token} if token else {}
            )
            out += data.get("itemInfoList", [])
            token = data.get("nextToken")
            if not token:
                return out

    async def add(self, list_id: str, name: str) -> dict:
        return await self._call(
            HTTPMethod.POST, f"{list_id}/items", body={"items": [{"itemType": "KEYWORD", "itemName": name}]}
        )

    async def update(self, list_id: str, item_id: str, version: int, attrs: list[dict]) -> dict:
        return await self._call(
            HTTPMethod.PUT,
            f"{list_id}/items/{item_id}",
            {"version": version},
            {"itemAttributesToUpdate": attrs, "itemAttributesToRemove": []},
        )

    async def delete(self, list_id: str, item_id: str, version: int) -> None:
        await self._call(HTTPMethod.DELETE, f"{list_id}/items/{item_id}", {"version": version})


def _find_item_id(obj: Any, name: str) -> str | None:
    """Dig the new item's id out of an add response, whatever its exact shape."""
    if isinstance(obj, dict):
        if "itemId" in obj and normalize(str(obj.get("itemName", name))) == normalize(name):
            return obj["itemId"]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            if found := _find_item_id(v, name):
                return found
    return None


class AlexaSide:
    """Synchronous adapter over AlexaClient for the sync engine."""

    label = "Alexa"

    def __init__(self, client: AlexaClient, run: Callable[[Coroutine], Any], list_id: str) -> None:
        self.client = client
        self.run = run
        self.list_id = list_id
        self._versions: dict[str, int] = {}

    def fetch(self) -> dict[str, Item]:
        rows = self.run(self.client.items(self.list_id))
        self._versions = {r["itemId"]: r["version"] for r in rows}
        return {r["itemId"]: Item(r["itemId"], r["itemName"], r["itemStatus"] == "COMPLETE") for r in rows}

    def add(self, name: str, completed: bool) -> str:
        before = set(self._versions)
        resp = self.run(self.client.add(self.list_id, name))
        item_id = _find_item_id(resp, name)
        if item_id is None:
            log.debug("add response had no itemId (%r); re-fetching to find it", resp)
            fresh = self.fetch()
            new = [i for i in fresh.values() if i.id not in before and normalize(i.name) == normalize(name)]
            if not new:
                raise RuntimeError(f"added {name!r} but could not find it afterwards")
            item_id = new[0].id
        if completed:
            self.update(item_id, name=None, completed=True)
        return item_id

    def _version(self, item_id: str) -> int:
        if item_id not in self._versions:
            self.fetch()
        return self._versions[item_id]

    def update(self, item_id: str, *, name: str | None, completed: bool | None) -> None:
        attrs = []
        if name is not None:
            attrs.append({"type": "itemName", "value": name})
        if completed is not None:
            attrs.append({"type": "itemStatus", "value": "COMPLETE" if completed else "ACTIVE"})
        self.run(self.client.update(self.list_id, item_id, self._version(item_id), attrs))
        self._versions.pop(item_id, None)  # version bumped; re-read if needed again

    def delete(self, item_id: str) -> None:
        self.run(self.client.delete(self.list_id, item_id, self._version(item_id)))
        self._versions.pop(item_id, None)


class LoginFailed(Exception):
    pass


async def interactive_login(email: str, password: str, otp: str, data_dir: str) -> dict[str, Any]:
    async with ClientSession() as session:
        api = make_api(session, email, password, None, data_dir)
        try:
            return await api.login.login_mode_interactive(otp)
        except CannotAuthenticate as exc:
            if "OTP code not found" in str(exc):
                raise LoginFailed(
                    "Amazon didn't ask for a 2-Step Verification code. alexa-sync requires 2SV with an "
                    "authenticator app: enable it under Your Account > Login & security, then try again. "
                    "(It can also mean the password was wrong or Amazon showed a CAPTCHA.)"
                ) from exc
            raise LoginFailed(f"Amazon rejected the login ({exc}). Check the password and code, then retry.") from exc

