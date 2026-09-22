"""alexa-sync: keep the Alexa shopping list and an Apple Reminders list in sync."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import getpass
import json
import logging
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import keyring

from . import amazon
from .reminders import RemindersSide
from .sync import Pair, TooManyDeletes, sync

log = logging.getLogger("alexa_sync")

DATA_DIR = Path.home() / "Library/Application Support/alexa-sync"
STATE_FILE = DATA_DIR / "state.json"
LOCK_FILE = DATA_DIR / "run.lock"
LOG_FILE = Path.home() / "Library/Logs/alexa-sync.log"
KEYCHAIN_SERVICE = "alexa-sync"
KEYCHAIN_ACCOUNT = "amazon-device"
AGENT_LABEL = "ms.willia.alexa-sync"
AGENT_PLIST = Path.home() / f"Library/LaunchAgents/{AGENT_LABEL}.plist"
ALERT_EVERY = 6 * 3600


# --- persistence -----------------------------------------------------------


def load_credentials() -> dict | None:
    raw = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    return json.loads(raw) if raw else None


def save_credentials(email: str, login_data: dict) -> None:
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, json.dumps({"email": email, "login_data": login_data}))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def alert(state: dict, key: str, message: str) -> None:
    """macOS notification, at most once per ALERT_EVERY for the same problem."""
    log.error(message)
    alerts = state.setdefault("alerts", {})
    if time.time() - alerts.get(key, 0) < ALERT_EVERY:
        return
    alerts[key] = time.time()
    script = f"display notification {json.dumps(message)} with title \"Alexa sync\""
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


# --- commands --------------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    email = args.email or input("Amazon email: ").strip()
    password = getpass.getpass("Amazon password: ")
    otp = input("2FA code from your authenticator: ").strip()
    login_data = asyncio.run(amazon.interactive_login(email, password, otp, str(DATA_DIR)))
    save_credentials(email, login_data)
    print("Logged in. Device registration saved to Keychain (password and code were not stored).")
    return 0


class DryRun:
    """Wraps a side so writes are logged instead of performed."""

    def __init__(self, side) -> None:
        self.side = side
        self.label = side.label
        self._n = 0

    def fetch(self):
        return self.side.fetch()

    def add(self, name, completed):
        print(f"[dry-run] {self.label}: add {name!r}")
        self._n += 1
        return f"dry-{self._n}"

    def update(self, item_id, *, name, completed):
        print(f"[dry-run] {self.label}: update {item_id} name={name!r} completed={completed!r}")

    def delete(self, item_id):
        print(f"[dry-run] {self.label}: delete {item_id}")


def cmd_run(args: argparse.Namespace) -> int:
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another run is in progress; skipping")
            return 0
        state = load_state()
        try:
            return _run(args, state)
        finally:
            if not args.dry_run:
                save_state(state)


def _run(args: argparse.Namespace, state: dict) -> int:
    creds = load_credentials()
    if not creds:
        alert(state, "auth", "Not logged in to Amazon. Run: alexa-sync login")
        return 2

    reminders = RemindersSide(args.list)
    try:
        state["reminders_list_id"] = reminders.resolve(state.get("reminders_list_id"))
    except RuntimeError as exc:
        alert(state, "list", str(exc))
        return 2

    with asyncio.Runner() as runner:
        session = runner.run(_make_session())
        try:
            api = amazon.make_api(session, creds["email"], "", creds["login_data"], str(DATA_DIR))
            client = amazon.AlexaClient(api)
            try:
                runner.run(client.connect())
                list_id = runner.run(client.shopping_list_id())
                alexa = amazon.AlexaSide(client, runner.run, list_id)

                a_side, r_side = (DryRun(alexa), DryRun(reminders)) if args.dry_run else (alexa, reminders)
                pairs = [Pair.from_dict(p) for p in state.get("pairs", [])]
                pairs = sync(a_side, r_side, pairs, max_deletes=args.max_deletes)
            except amazon.AuthExpired:
                alert(state, "auth", "Amazon login expired. Run: alexa-sync login")
                return 2
            except TooManyDeletes as exc:
                alert(state, "deletes", f"Sync paused: {exc}")
                return 3
            finally:
                if client.login_data_changed and not args.dry_run:
                    save_credentials(creds["email"], client.login_data)
        finally:
            runner.run(session.close())

    if not args.dry_run:
        state["pairs"] = [p.to_dict() for p in pairs]
        state.get("alerts", {}).clear()
    log.info("synced %d paired items", len(pairs))
    return 0


async def _make_session():
    from aiohttp import ClientSession

    return ClientSession()


def cmd_install(args: argparse.Namespace) -> int:
    exe = shutil.which("alexa-sync") or sys.argv[0]
    exe = str(Path(exe).resolve())
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": AGENT_LABEL,
        "ProgramArguments": [exe, "run", "--list", args.list],
        "StartInterval": args.interval,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    }
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{AGENT_LABEL}"], capture_output=True)
    AGENT_PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(AGENT_PLIST)], check=True)
    print(f"Installed {AGENT_PLIST} (every {args.interval}s). Logs: {LOG_FILE}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{AGENT_LABEL}"], capture_output=True)
    AGENT_PLIST.unlink(missing_ok=True)
    print("Uninstalled.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="alexa-sync", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="register this Mac with Amazon (one time)")
    p.add_argument("--email")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("run", help="sync once")
    p.add_argument("--list", default="Groceries", help="Reminders list name (default: Groceries)")
    p.add_argument("--max-deletes", type=int, default=5, help="abort if a pass would delete more than this")
    p.add_argument("--dry-run", action="store_true", help="show what would change without changing anything")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("install", help="run automatically via launchd")
    p.add_argument("--list", default="Groceries")
    p.add_argument("--interval", type=int, default=120, help="seconds between runs (default: 120)")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("uninstall", help="remove the launchd job")
    p.set_defaults(fn=cmd_uninstall)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("aioamazondevices").setLevel(logging.WARNING)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
