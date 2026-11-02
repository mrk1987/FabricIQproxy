"""
fabriciq_proxy.py - local MCP proxy for Microsoft Fabric IQ MCP.

Claude Desktop / Claude Code launches this as a local stdio server.
The proxy signs in to Entra with MSAL (without the resource parameter sent
by Claude's OAuth client), renews tokens automatically, and forwards tool
calls to Fabric IQ with the signed-in user's token.

Usage:
    python fabriciq_proxy.py login     # sign in through the browser
    python fabriciq_proxy.py status    # show the signed-in account
    python fabriciq_proxy.py logout    # remove cached accounts
    python fabriciq_proxy.py           # run as an MCP server (launched by Claude)

Environment variables:
    FABRICIQ_CLIENT_ID    Application (client) ID for your own Entra app
    FABRICIQ_TENANT       (optional) Tenant ID/domain; defaults to organizations
    FABRICIQ_LOGIN_HINT   (optional) Account to suggest during sign-in
    FABRICIQ_URL          (optional) Endpoint; defaults to Fabric IQ
    FABRICIQ_VARIANT      (optional) Tool contract; defaults to Fabric.Routing.FabricIQ.V1
    FABRICIQ_LOG          (optional) Log file path

Important: stdout belongs to the MCP protocol. Logs go to stderr/a file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import httpx
import msal
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

__version__ = "1.0.0"

DEFAULT_URL = "https://fabriciq.svc.cloud.microsoft/v1/mcp/fabriciq"
DEFAULT_VARIANT = "Fabric.Routing.FabricIQ.V1"
# This Microsoft first-party app cannot consent to Fabric IQ's Power BI scopes.
UNSUPPORTED_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
DEFAULT_TENANT = "organizations"
POWERBI_API = "https://analysis.windows.net/powerbi/api"
SCOPES = [f"{POWERBI_API}/Item.Read.All", f"{POWERBI_API}/Item.Execute.All"]

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "fabriciq-proxy"
CACHE_FILE = CACHE_DIR / "token_cache.bin"

log = logging.getLogger("fabriciq-proxy")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_path = os.environ.get("FABRICIQ_LOG")
    if log_path:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # Reduce MSAL/httpx logging; tokens must never appear in INFO logs.
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def configured_provider() -> TokenProvider:
    return TokenProvider(
        os.environ.get("FABRICIQ_CLIENT_ID"),
        os.environ.get("FABRICIQ_TENANT"),
        os.environ.get("FABRICIQ_LOGIN_HINT", "").strip() or None,
    )


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def build_token_cache():
    """Encrypted token cache (DPAPI on Windows, Keychain on macOS).

    There is no plaintext fallback. If encryption is unavailable, stop
    rather than write an unencrypted refresh token to disk.
    """
    from msal_extensions import PersistedTokenCache, build_encrypted_persistence

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        persistence = build_encrypted_persistence(str(CACHE_FILE))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Encrypted token-cache storage is unavailable on this machine "
            f"({exc}). The proxy does not store tokens in plaintext."
        ) from exc
    return PersistedTokenCache(persistence)


class AuthenticationError(RuntimeError):
    """Authentication requires explicit user action, never an automatic retry."""


class TokenProvider:
    """Acquire and renew tokens through MSAL. Thread-safe."""

    def __init__(self, client_id: str | None = None, tenant: str | None = None,
                 login_hint: str | None = None):
        self._lock = threading.Lock()
        self._login_hint = login_hint
        client_id = (client_id or "").strip()
        self._app = None
        self._configuration_error = (
            "Configure FABRICIQ_CLIENT_ID with your own Entra public-client app "
            "and Power BI delegated permissions Item.Read.All and Item.Execute.All. "
            "The Microsoft development app cannot authenticate to Fabric IQ (AADSTS65002). "
            "See README.md."
        )
        # Keep stdio MCP alive to report configuration errors without opening a browser.
        if not client_id or client_id.lower() == UNSUPPORTED_CLIENT_ID:
            return
        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{(tenant or '').strip() or DEFAULT_TENANT}",
            token_cache=build_token_cache(),
        )

    def _account(self):
        if self._app is None:
            raise AuthenticationError(self._configuration_error)
        accounts = self._app.get_accounts()
        if self._login_hint:
            for acc in accounts:
                if acc.get("username", "").lower() == self._login_hint.lower():
                    return acc
            return None
        return accounts[0] if accounts else None

    def silent(self) -> str | None:
        """Get a cached token, refreshing when needed, without user interaction."""
        with self._lock:
            account = self._account()
            if not account:
                return None
            result = self._app.acquire_token_silent(SCOPES, account=account)
            if result and "access_token" in result:
                return result["access_token"]
            if result and "error" in result:
                log.warning("Silent token renewal failed: %s", result.get("error"))
            return None

    def interactive(self) -> str:
        """Open the browser for sign-in. Redirect: http://localhost."""
        if self._app is None:
            raise AuthenticationError(self._configuration_error)
        with self._lock:
            result = self._app.acquire_token_interactive(
                SCOPES,
                login_hint=self._login_hint,
                prompt="select_account",
                timeout=300,
            )
        if "access_token" not in result:
            raise AuthenticationError(
                f"Sign-in failed: {result.get('error')}: "
                f"{result.get('error_description', '').splitlines()[0] if result.get('error_description') else ''}"
            )
        return result["access_token"]

    def get(self, allow_interactive: bool) -> str:
        token = self.silent()
        if token:
            return token
        if not allow_interactive:
            raise AuthenticationError("Not signed in. Run: python fabriciq_proxy.py login")
        log.info("No valid sign-in in the cache; opening the browser.")
        return self.interactive()

    def who(self) -> str | None:
        account = self._account()
        return account.get("username") if account else None

    def logout(self) -> int:
        if self._app is None:
            raise AuthenticationError(self._configuration_error)
        with self._lock:
            accounts = self._app.get_accounts()
            for acc in accounts:
                self._app.remove_account(acc)
            return len(accounts)


class BearerAuth(httpx.Auth):
    """Set a fresh Authorization header on every request to Fabric IQ.

    MSAL returns a cached token until it nears expiry, then attempts silent
    renewal with the refresh token. Failed renewal requires explicit sign-in.
    """

    def __init__(self, provider: TokenProvider):
        self._provider = provider

    def sync_auth_flow(self, request: httpx.Request):
        token = self._provider.silent()
        if not token:
            raise AuthenticationError("Not signed in. Run: python fabriciq_proxy.py login")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request

    async def async_auth_flow(self, request: httpx.Request):
        # MSAL can acquire locks and perform disk/network I/O during renewal.
        token = await asyncio.to_thread(self._provider.silent)
        if not token:
            raise AuthenticationError("Not signed in. Run: python fabriciq_proxy.py login")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


# --------------------------------------------------------------------------
# Fabric IQ connection
# --------------------------------------------------------------------------

class Upstream:
    """MCP client session owned by one long-lived background task.

    The MCP client's anyio task groups must be opened and closed in the same
    task. supervise() owns the connection; tool handlers request a session
    and wait. Operations reconnect once on failure, except for authentication.
    """

    def __init__(self, url: str, variant: str, provider: TokenProvider):
        self._url = url
        self._variant = variant
        self._provider = provider
        self._session: ClientSession | None = None
        self._error: BaseException | None = None
        self._want = asyncio.Event()
        self._ready = asyncio.Event()
        self._drop = asyncio.Event()
        self._closed = asyncio.Event()
        self._closed.set()
        self._lock = asyncio.Lock()

    async def supervise(self) -> None:
        while True:
            await self._want.wait()
            self._want.clear()
            self._closed.clear()
            try:
                # Only the explicit CLI login command may open a browser. Clients
                # can repeat tools/list automatically, including after restarts.
                await asyncio.to_thread(self._provider.get, False)
                async with AsyncExitStack() as stack:
                    read, write, _ = await stack.enter_async_context(
                        streamablehttp_client(
                            self._url,
                            headers={"X-Variants": self._variant},
                            auth=BearerAuth(self._provider),
                            timeout=60,
                            sse_read_timeout=600,
                        )
                    )
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    self._session = session
                    log.info("Connected to Fabric IQ as %s", self._provider.who())
                    self._ready.set()
                    await self._drop.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._error = exc
                log.warning("Fabric IQ connection failed: %s", exc)
            finally:
                self._session = None
                self._drop.clear()
                self._ready.set()
                self._closed.set()

    async def session(self) -> ClientSession:
        async with self._lock:
            if self._session is not None:
                return self._session
            self._error = None
            self._ready.clear()
            self._want.set()
            await self._ready.wait()
            if self._session is None:
                raise self._error or RuntimeError("Could not connect to Fabric IQ.")
            return self._session

    async def reset(self) -> None:
        async with self._lock:
            if self._session is None:
                return
            self._drop.set()
            await self._closed.wait()

    async def run(self, op):
        """Run an operation, retrying once after reconnecting unless auth fails."""
        try:
            return await op(await self.session())
        except AuthenticationError:
            raise
        except Exception as first:  # noqa: BLE001
            log.warning("Fabric IQ call failed (%s); reconnecting.", first)
            await self.reset()
            return await op(await self.session())


# --------------------------------------------------------------------------
# Local stdio MCP server used by Claude
# --------------------------------------------------------------------------

def build_server(upstream: Upstream) -> Server:
    server: Server = Server("fabriciq-proxy", version=__version__)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        result = await upstream.run(lambda s: s.list_tools())
        return list(result.tools)

    @server.call_tool(validate_input=False)  # Fabric IQ validates the input
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        try:
            return await upstream.run(lambda s: s.call_tool(name, arguments))
        except Exception as exc:  # noqa: BLE001
            log.error("Tool call %s failed: %s", name, exc)
            return types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=friendly_error(exc))],
            )

    return server


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "Not signed in" in text or "Sign-in failed" in text:
        return f"{text}. Sign in again with: python fabriciq_proxy.py login"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return "Fabric IQ rejected the token (401). Run login again and check that the app has Item.Read.All and Item.Execute.All."
        if code == 403:
            return "Access denied (403). The user does not have access to the Power BI content."
        if code == 429:
            return "Power BI is throttling requests (429). Wait and try again with a narrower query."
    return f"Fabric IQ error: {text}"


async def serve() -> None:
    provider = configured_provider()
    upstream = Upstream(
        os.environ.get("FABRICIQ_URL") or DEFAULT_URL,
        os.environ.get("FABRICIQ_VARIANT") or DEFAULT_VARIANT,
        provider,
    )
    server = build_server(upstream)
    async with anyio.create_task_group() as tg:
        tg.start_soon(upstream.supervise)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
        tg.cancel_scope.cancel()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def cli(command: str) -> int:
    provider = configured_provider()
    if command == "login":
        provider.interactive()
        print(f"Signed in as {provider.who()}. Tokens are stored encrypted in {CACHE_DIR}.")
        return 0
    if command == "status":
        who = provider.who()
        if not who:
            print("Not signed in.")
            return 1
        ok = provider.silent() is not None
        print(f"Account: {who}. Token: {'valid' if ok else 'expired; run login'}.")
        return 0 if ok else 1
    if command == "logout":
        n = provider.logout()
        print(f"Removed {n} account(s) from the cache.")
        return 0
    print(f"Unknown command: {command}. Use login, status, or logout.")
    return 2


def main() -> None:
    setup_logging()
    if len(sys.argv) > 1:
        try:
            sys.exit(cli(sys.argv[1].lower()))
        except AuthenticationError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
