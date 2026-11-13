"""Amazon's relative sign-in forms must reach HTTP as absolute URLs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yarl import URL

from alexa_sync.amazon import _resolve_relative_login_urls


@pytest.mark.parametrize(('action', 'expected'), [
    ('/ax/claim?test=1', 'https://www.amazon.com/ax/claim?test=1'),
    ('claim', 'https://www.amazon.com/ap/claim'),
    ('https://www.amazon.com/ap/signin', 'https://www.amazon.com/ap/signin'),
])
def test_form_action_uses_actual_response_url(action, expected):
    request = AsyncMock(return_value=(None, SimpleNamespace(url=URL('https://www.amazon.com/ap/start'))))
    api = SimpleNamespace(
        _http_wrapper=SimpleNamespace(session_request=request),
        login=SimpleNamespace(_session_state_data=SimpleNamespace(retail_site_url=URL('https://www.amazon.com'))),
    )
    _resolve_relative_login_urls(api)

    async def flow():
        await api._http_wrapper.session_request(method='GET', url=URL('https://www.amazon.com/ap/signin'))
        await api._http_wrapper.session_request(method='POST', url=URL(action), input_data={'email': 'synthetic@example.test'})

    asyncio.run(flow())
    assert request.call_args.kwargs == {
        'url': URL(expected), 'method': 'POST', 'input_data': {'email': 'synthetic@example.test'},
    }


def test_email_first_login_submits_password_only_on_password_page():
    from bs4 import BeautifulSoup
    from alexa_sync.amazon import make_api, _support_staged_amazon_login
    from aiohttp import ClientSession

    async def flow():
        async with ClientSession() as session:
            api = make_api(session, 'synthetic@example.test', 'test-password', None, '/tmp')
            pages = [
                '<form name="signIn" method="post" action="/ax/claim"><input name="email"><input name="password" id="auth-credential-autofill-hint"></form>',
                '<form name="signIn" method="post" action="/ap/signin"><input name="password" id="ap_password"></form>',
                '<form><input id="auth-mfa-otpcode"></form>',
            ]
            request = AsyncMock(side_effect=[(BeautifulSoup(p, 'html.parser'), SimpleNamespace(url=URL('https://www.amazon.com/ap/signin'))) for p in pages])
            api._http_wrapper.session_request = request
            _resolve_relative_login_urls(api)
            _support_staged_amazon_login(api)
            _, soup = await api.login._oauth_login(b'synthetic')
            assert soup.find('input', id='auth-mfa-otpcode')
            assert request.call_count == 3
            assert 'password' not in request.call_args_list[1].kwargs['input_data']
            assert request.call_args_list[2].kwargs['input_data']['password'] == 'test-password'

    asyncio.run(flow())
