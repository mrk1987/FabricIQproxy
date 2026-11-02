import asyncio
import os
import threading
import unittest
from unittest.mock import Mock, AsyncMock, patch

import httpx

from fabriciq_proxy import (BearerAuth, TokenProvider, configured_provider,
                            UNSUPPORTED_CLIENT_ID, AuthenticationError, Upstream)


class ConfigurationTests(unittest.TestCase):
    def make_provider(self, environment):
        with patch.dict(os.environ, environment, clear=True), \
                patch("fabriciq_proxy.build_token_cache") as cache, \
                patch("fabriciq_proxy.msal.PublicClientApplication") as app:
            provider = configured_provider()
            return provider, app.call_args, cache.return_value

    def test_missing_client_id_reports_setup_without_constructing_msal(self):
        provider, call, _ = self.make_provider({})
        self.assertIsNone(call)
        with self.assertRaisesRegex(AuthenticationError, "FABRICIQ_CLIENT_ID"):
            provider.get(False)

    def test_blank_configuration_never_opens_browser(self):
        provider, call, _ = self.make_provider({
            "FABRICIQ_CLIENT_ID": " ", "FABRICIQ_TENANT": "", "FABRICIQ_LOGIN_HINT": " "})
        self.assertIsNone(call)
        with self.assertRaises(AuthenticationError):
            provider.interactive()
        self.assertIsNone(provider._login_hint)

    def test_explicit_settings_override_defaults(self):
        provider, call, _ = self.make_provider({
            "FABRICIQ_CLIENT_ID": " custom-app ", "FABRICIQ_TENANT": " example.com ",
            "FABRICIQ_LOGIN_HINT": " user@example.com "})
        self.assertEqual(call.args, ("custom-app",))
        self.assertEqual(call.kwargs["authority"], "https://login.microsoftonline.com/example.com")
        self.assertEqual(provider._login_hint, "user@example.com")

    def test_tenant_is_optional_with_custom_client_id(self):
        _, call, _ = self.make_provider({"FABRICIQ_CLIENT_ID": "custom-app"})
        self.assertEqual(call.args, ("custom-app",))
        self.assertEqual(call.kwargs["authority"], "https://login.microsoftonline.com/organizations")

    def test_known_unsupported_app_is_rejected_before_browser_login(self):
        provider, call, _ = self.make_provider({"FABRICIQ_CLIENT_ID": UNSUPPORTED_CLIENT_ID})
        self.assertIsNone(call)
        with self.assertRaisesRegex(AuthenticationError, "AADSTS65002"):
            provider.interactive()


class AuthenticationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_discovery_never_opens_browser_or_retries_authentication(self):
        provider = Mock()
        provider.get.side_effect = AuthenticationError("Run login explicitly")
        upstream = Upstream("https://example.com", "test", provider)
        operation = AsyncMock()
        task = asyncio.create_task(upstream.supervise())
        try:
            for _ in range(3):
                with self.assertRaises(AuthenticationError):
                    await asyncio.wait_for(upstream.run(operation), timeout=2)
            self.assertEqual(provider.get.call_count, 3)
            for call in provider.get.call_args_list:
                self.assertEqual(call.args, (False,))
            provider.interactive.assert_not_called()
            operation.assert_not_awaited()
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class AccountSelectionTests(unittest.TestCase):
    def provider(self, hint):
        # Avoid constructing MSAL or accessing the real encrypted token cache.
        provider = TokenProvider.__new__(TokenProvider)
        provider._lock = threading.Lock()
        provider._login_hint = hint
        provider._app = Mock()
        provider._app.get_accounts.return_value = [
            {"username": "admin@example.com"},
            {"username": "user@example.com"},
        ]
        return provider

    def test_unmatched_hint_never_uses_another_cached_user(self):
        provider = self.provider("missing@example.com")
        self.assertIsNone(provider.who())
        self.assertIsNone(provider.silent())
        provider._app.acquire_token_silent.assert_not_called()
        with self.assertRaises(RuntimeError):
            provider.get(False)

    def test_hint_matches_case_insensitively(self):
        provider = self.provider("USER@EXAMPLE.COM")
        provider._app.acquire_token_silent.return_value = {"access_token": "test-token"}
        self.assertEqual(provider.silent(), "test-token")
        provider._app.acquire_token_silent.assert_called_once()
        self.assertEqual(
            provider._app.acquire_token_silent.call_args.kwargs["account"],
            {"username": "user@example.com"},
        )

    def test_no_hint_keeps_first_account_behavior(self):
        self.assertEqual(self.provider(None).who(), "admin@example.com")

    def test_empty_cache_has_no_account(self):
        provider = self.provider(None)
        provider._app.get_accounts.return_value = []
        self.assertIsNone(provider.who())


class AsyncAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_renewal_allows_other_event_loop_work(self):
        entered = threading.Event()
        release = threading.Event()
        loop_thread = threading.get_ident()
        worker_threads = []

        def silent():
            worker_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Event loop did not release token renewal")
            return "test-token"

        provider = Mock()
        provider.silent.side_effect = silent

        def handle(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200)

        async with httpx.AsyncClient(
            auth=BearerAuth(provider), transport=httpx.MockTransport(handle)
        ) as client:
            request_task = asyncio.create_task(client.get("https://example.com"))
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                # This coroutine must run before renewal can complete.
                self.assertFalse(request_task.done())
                self.assertNotEqual(worker_threads, [loop_thread])
                release.set()
                self.assertEqual((await request_task).status_code, 200)
            finally:
                release.set()
                if not request_task.done():
                    await request_task

    async def test_missing_token_does_not_send_request(self):
        provider = Mock()
        provider.silent.return_value = None
        handler = Mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient(
            auth=BearerAuth(provider), transport=httpx.MockTransport(handler)
        ) as client:
            with self.assertRaises(RuntimeError):
                await client.get("https://example.com")
        handler.assert_not_called()


class SyncAuthTests(unittest.TestCase):
    def test_sync_client_still_authenticates(self):
        provider = Mock()
        provider.silent.return_value = "test-token"

        def handle(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200)

        with httpx.Client(auth=BearerAuth(provider), transport=httpx.MockTransport(handle)) as client:
            self.assertEqual(client.get("https://example.com").status_code, 200)


if __name__ == "__main__":
    unittest.main()
