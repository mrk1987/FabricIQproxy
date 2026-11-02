# About this skill

`SKILL.md` and `LICENSE` were copied unchanged from Microsoft's repository,
[microsoft/skills-for-fabric](https://github.com/microsoft/skills-for-fabric/tree/main/skills/fabriciq)
(version 0.1.1, retrieved November 2, 2026). They are distributed under the MIT
license; see `LICENSE`.

The skill links to `../../common/COMMON-CORE.md` and `COMMON-CLI.md` for
authentication. Those files are intentionally omitted: `fabriciq_proxy.py`
handles authentication, and Claude should not run `az login` or retrieve tokens
itself. Configure your own Entra app and run the proxy's `login` command explicitly.
MCP tool discovery and calls never open a sign-in browser automatically.

To update the skill, download `skills/fabriciq/SKILL.md` from the upstream
repository and replace the bundled file. Rebuild `desktop-skills/fabriciq.zip`
with `SKILL.md`, `LICENSE`, and this notice so the Desktop package stays in sync.
