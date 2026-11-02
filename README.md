# FabricIQproxy

> **Experimental - live Fabric IQ access is still untested.** Local automated
> tests and the stdio MCP handshake have passed, but successful sign-in and
> end-to-end queries against real Fabric/Power BI content have not been verified.
> The proxy is not yet validated for production use.

A local stdio MCP proxy for Microsoft Fabric IQ, usable with Claude Code,
Claude Desktop, and other clients that support stdio MCP servers. The proxy uses
MSAL to sign in with your Microsoft Entra account, renews tokens, and forwards
Fabric IQ tool discovery and calls with your delegated permissions.

## Requirements

- Python 3.11 or newer.
- A Microsoft Entra work or school account.
- Access to the Fabric/Power BI content you want to query.
- Encrypted token storage: Windows DPAPI, macOS Keychain, or Linux libsecret.
  There is no plaintext token-cache fallback.

Sign-in requires your own Entra application client ID. The previously used Microsoft
development app cannot obtain these Power BI permissions: Entra rejects it with
AADSTS65002 because first-party API access requires Microsoft preauthorization.
There is no working built-in client ID for this proxy. Tenant defaults to
`organizations` for multitenant apps; single-tenant apps need their tenant ID/domain.
Access remains limited to your existing Fabric permissions and consented scopes.

## Register an Entra application

1. In the Microsoft Entra admin center, open **App registrations > New registration**.
2. Choose a name such as `FabricIQ local proxy`. For use in your organization,
   choose **Accounts in this organizational directory only**.
3. Copy the **Application (client) ID** and **Directory (tenant) ID** from Overview.
4. Under **Authentication**, add the **Mobile and desktop applications** platform
   and the redirect URI `http://localhost`.
5. Under **API permissions > Add a permission > APIs my organization uses**,
   select **Power BI Service > Delegated permissions** and add `Item.Read.All`
   and `Item.Execute.All`.
6. Obtain consent as required by your tenant. User consent may be permitted;
   an administrator may need to approve it under your organization's policies.

No client secret is used. The app identifies the proxy during sign-in; data access
still uses the signed-in user's permissions. See Microsoft's
[Fabric IQ app registration instructions](https://learn.microsoft.com/en-us/fabric/iq/connectors/fabric-iq-mcp#clients-that-require-an-app-registration).

## Install on Windows

From the project directory:

```powershell
.\install.ps1 -ClientId '<application-id>' -Tenant 'example.com' -ConfigureDesktop
```

`ClientId` is required for sign-in. `Tenant` and `LoginHint` are optional, also read from
`FABRICIQ_CLIENT_ID`, `FABRICIQ_TENANT`, and `FABRICIQ_LOGIN_HINT`.
Omit `-ConfigureDesktop` for project configuration only, or use
`-SkipLogin` to configure without a client ID or opening a browser. Before use,
add the app settings to your client configuration and run `login` explicitly.

The installer creates `.venv`, installs dependencies, and merges `fabriciq` into
`.mcp.json`. It preserves other servers and top-level settings and backs up
existing configuration before writing. The same applies to Claude Desktop when
requested. Generated paths and application/tenant settings are local to your
installation; do not commit those changes. No tokens are stored in MCP config.

Start Claude Code from the project directory and approve the `fabriciq` server.
For Claude Desktop, fully quit and reopen the app after configuration.

### Claude Desktop

Run the installer with `-ConfigureDesktop`, your client ID, and your tenant ID as
shown above. It writes absolute executable/script paths and literal app settings
to Desktop's configuration while preserving existing servers and settings.
The installer completes browser login unless `-SkipLogin` is supplied.

After installation, fully quit Desktop, including its system tray icon, and
reopen it. Enable `fabriciq` in the MCP tools. Desktop uses the encrypted login
cache; it will not open a browser itself. If login expires and cannot renew silently,
run the manual `login` command with the same client ID, tenant, and account hint,
then reconnect the server.

## Manual setup and other MCP clients

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:FABRICIQ_CLIENT_ID = '<your-application-client-id>'
$env:FABRICIQ_TENANT = 'example.com' # required for single-tenant apps
$env:FABRICIQ_LOGIN_HINT = 'user@example.com' # optional
.\.venv\Scripts\python fabriciq_proxy.py login
```

Configure your client to run the absolute path to `.venv\Scripts\python.exe`
(or `.venv/bin/python` on macOS/Linux), with the absolute path to
`fabriciq_proxy.py` as its argument. Pass the same environment variables to
that process. The checked-in `.mcp.json` is a Claude Code template that uses
environment-variable expansion; `install.ps1` writes absolute paths for you.
Other clients may require literal values instead of that expansion syntax.

## Configuration

| Variable | Purpose |
|---|---|
| `FABRICIQ_CLIENT_ID` | Entra application ID required for sign-in; no built-in fallback |
| `FABRICIQ_TENANT` | Optional tenant ID/domain; defaults to `organizations` for work or school account selection |
| `FABRICIQ_LOGIN_HINT` | Optional account username; a missing cache match requires login rather than using another cached account |
| `FABRICIQ_URL` | Optional endpoint; default `https://fabriciq.svc.cloud.microsoft/v1/mcp/fabriciq` |
| `FABRICIQ_VARIANT` | Optional contract; default `Fabric.Routing.FabricIQ.V1` |
| `FABRICIQ_LOG` | Optional log file; logging otherwise goes to stderr |

Use only a trusted `FABRICIQ_URL`: the proxy sends your bearer token to it.
Tokens are stored encrypted in `%LOCALAPPDATA%\fabriciq-proxy` on Windows,
or `~/.cache/fabriciq-proxy` elsewhere, for the current OS user. Account caches
can contain multiple users; use `FABRICIQ_LOGIN_HINT` to select one explicitly.
When no hint is configured, the first cached account is used. `logout` removes
all accounts from this proxy's cache.

## Commands

Set the client ID and any tenant/account settings to the same values as your MCP client:

```powershell
.\.venv\Scripts\python fabriciq_proxy.py login
.\.venv\Scripts\python fabriciq_proxy.py status
.\.venv\Scripts\python fabriciq_proxy.py logout
```

Without a subcommand, the script runs as a stdio MCP server. Only the explicit
`login` command opens a browser. Tool discovery and tool calls return authentication
errors without prompting or retrying authentication. Token renewal runs in a worker thread
so it does not block the MCP event loop, following [HTTPX authentication guidance](https://www.python-httpx.org/advanced/authentication/).

## Optional skill

`.claude/skills/fabriciq/` contains Microsoft's Fabric IQ skill and MIT license.
`desktop-skills/fabriciq.zip` packages it for Claude Desktop. This skill is
optional; other MCP clients can use the proxy directly. See the bundled
`NOTICE.md` for attribution and update instructions.

## Troubleshooting

| Symptom | Action |
|---|---|
| AADSTS65002 / missing client ID | Configure your own app with `FABRICIQ_CLIENT_ID` and, for a single-tenant app, `FABRICIQ_TENANT`; the Microsoft development app cannot be used |
| Repeated browser sign-in from an older proxy process | Fully quit and reopen the MCP client to load the updated proxy. Automatic browser login has been removed; configure your own app, then run `login` explicitly |
| Not logged in / expired token | Run `login` with the same configuration and account hint |
| AADSTS50011 | Check the app's `http://localhost` desktop redirect URI |
| AADSTS65001 | Obtain consent for the delegated permissions |
| AADSTS7000218 | Check the public-client authentication settings in Entra |
| 401 | Check delegated permissions and sign in again |
| 403 | Check the signed-in user's access to the requested Fabric content |
| 429 | Wait before retrying and reduce query size |
| Encrypted persistence unavailable | Configure your OS credential store; tokens will not be saved as plaintext |
| Server missing from client | Check executable/script paths, environment variables, and client MCP logs |

The proxy forwards the upstream tools; it does not enforce a separate read-only
policy. Apply your organization's data-sharing rules and Fabric permissions.

## Development

```powershell
.\.venv\Scripts\python -m py_compile fabriciq_proxy.py
.\.venv\Scripts\python -m unittest discover -s tests -v
powershell -NoProfile -File tests\test_install.ps1
```

Tests use mocked authentication and local HTTP transport; no tenant login or
live Fabric content is required. Live validation requires your own configuration.
