# FabricIQproxy

A reusable local stdio MCP proxy for Microsoft Fabric IQ. Sign-in requires a
user-configured Entra app. Tenant defaults to organizations for multitenant apps.
There is no built-in client ID: the Microsoft development app fails with AADSTS65002.
No organization-specific
credentials or data model names belong in this repository.

## Working on the proxy

- stdout belongs to MCP in server mode. Use `log` (stderr or `FABRICIQ_LOG`).
- `Upstream.supervise()` owns the upstream MCP session. Open and close anyio
  contexts in that same task, not in tool handlers.
- MSAL tokens must use encrypted `msal-extensions` persistence. No plaintext
  fallback, tokens in configuration/environment variables, or token logging.
- Keep blocking MSAL operations off the asyncio event loop.
- Only explicit CLI login may open a browser. Never retry authentication failures.
- An explicit `FABRICIQ_LOGIN_HINT` must never fall back to another cached user.
- Default delegated scopes: `Item.Read.All` and `Item.Execute.All` for Power BI.
- Forward Fabric IQ tools; do not add unrelated APIs or local write/export tools.
- Installers must preserve other MCP servers/settings and back up existing files.

## Validation

```powershell
.\.venv\Scripts\python -m py_compile fabriciq_proxy.py
.\.venv\Scripts\python -m unittest discover -s tests -v
powershell -NoProfile -File tests\test_install.ps1
```

Live tool calls require a configured Entra app, login, and tenant consent.
The optional Microsoft Fabric IQ skill is in `.claude/skills/fabriciq/`.
