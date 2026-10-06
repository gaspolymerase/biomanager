# Plan: BioManager as a connector in claude.ai, Claude's apps and ChatGPT

Status: built (not yet released). Follows `docs/plans/ai-assistants.md`, whose
local MCP server (`mcp/biomanager_mcp.py`) needs Python and a copy of the
source on the person's computer, and can't be used from claude.ai on the
web, Claude's phone app or ChatGPT.

## In short

- The lab server answers MCP itself, at `https://<server>/api/v1/mcp`, with
  the same tools as the local server. Nothing to install.
- From claude.ai, Claude Desktop, Claude's phone app and ChatGPT ("custom
  connector"): those apps' servers make the calls, from the internet. So it
  works when the lab has turned on internet access (Tailscale Funnel,
  `deploy/host/internet-access.sh`), at `https://<server>:8443/api/v1/mcp`.
  They sign in with OAuth, as those apps require.
- From Claude Code (or any MCP client that sends a header), on any computer
  that can reach the server, internet access or not, the desktop app too:
  the same address with `Authorization: Bearer <token>`. No OAuth needed.
- What the assistant may do is unchanged: read, and propose; the person
  approves on Proposed changes, signed in. A connector's token is a Read and
  propose token, listed under Settings → API tokens, where it can be revoked.

## What those apps require (checked October 2026)

Claude ([connector authentication](https://claude.com/docs/connectors/building/authentication)):
OAuth 2.0 with Dynamic Client Registration (RFC 7591) or a Client ID
Metadata Document; PKCE S256 on every request; a `401` with
`WWW-Authenticate: Bearer resource_metadata="…"` to start sign-in;
protected resource metadata (RFC 9728) whose `resource` equals the URL the
person enters and whose first `authorization_servers` entry is the issuer;
authorization server metadata (RFC 8414) at `/.well-known/`; the redirect
`https://claude.ai/api/mcp/auth_callback`, and for Claude Code a loopback
redirect on any port (`http://localhost/callback`, `http://127.0.0.1/callback`);
the token endpoint takes form-encoded bodies, answers within 10 seconds,
and refresh tokens rotate (`invalid_grant` when one is no longer good). The
consent screen shows the redirect's host. Calls come from 160.79.104.0/21.

ChatGPT (developer mode, custom connectors): the same OAuth with DCR and
PKCE; with RFC 9207 issuer identification (`iss` in every authorization
response, `authorization_response_iss_parameter_supported: true`, the same
issuer string everywhere) it uses a stable redirect URI.

Transport: MCP's streamable HTTP. One `POST` per JSON-RPC message, answered
with JSON; no server-sent stream (`GET` is `405`); no session. Protocol
versions 2025-03-26, 2025-06-18 and 2025-11-25.

## Design

1. **Shared tools** (`app/assistant_tools.py`, standard library only): the
   instructions, prompts, each tool's description and input schema, and the
   functions over an `Api`. The local server (`mcp/biomanager_mcp.py`)
   imports them; so does the endpoint. One description, two transports.
2. **The endpoint** (`app/mcp_http.py`, `POST /api/v1/mcp`): JSON-RPC
   `initialize`, `ping`, `tools/list`, `tools/call`, `prompts/list`,
   `prompts/get`; notifications get `202`. It is inside `/api/v1`, so the
   token, the rate limit and the switched-off-database rules apply as for
   any API call; Read and propose tokens may use it. A tool calls the API
   in-process with the caller's own token, so it can never do more than the
   API does. Every `401` from `/api/v1` points at the resource metadata.
3. **OAuth** (`app/oauth.py`, revision `0018_oauth`):
   - `/.well-known/oauth-protected-resource` (also under `/api/v1/mcp`) and
     `/.well-known/oauth-authorization-server`. The issuer is the address
     the request came in on: the public one (`BIOMANAGER_PUBLIC_URL`) from
     the internet, the lab's own from inside.
   - `POST /oauth/register` (DCR): any `https` redirect, or a loopback one;
     public clients (`none`) or a client secret. Clients nobody used for 30
     days are removed.
   - `GET/POST /oauth/authorize`: checks client, redirect (loopback matched
     without its port), PKCE S256 and `resource`. Signed in (on the lab's
     network): a consent page, "Claude wants to read your lab and propose
     changes for you to approve", naming where it sends you back. From the
     internet, where the lab never shows its sign-in form, the page asks
     instead for a **connection code**: made under Settings → API tokens →
     Connect an AI assistant, for this person, used once, valid 10 minutes,
     wrong guesses throttled like guest codes. Every answer carries `iss`.
   - `POST /oauth/token`: the code (once, 5 minutes, PKCE checked, the
     same redirect) gives an access token, which is a Read and propose
     `api_tokens` row named after the client ("Claude (connector)"), valid
     an hour, and a refresh token valid 90 days. A refresh rotates both:
     the same row gets a new token, so Settings lists one row per
     connection; revoking that row ends the connection (`invalid_grant`).
   - The internet gate (`app/guests.py`) lets `/api/v1/`, `/oauth/` and
     `/.well-known/oauth-` through without a session: each answers only
     with a token, a code, or public metadata.
4. **Settings → API tokens → Connect an AI assistant** shows three ways in:
   the connector address and a connection code for claude.ai or ChatGPT
   (when the lab is on the internet), the Claude Code command (the address
   and the token), and the local server's setup as before.

## Tests

The OAuth flow end to end (register, authorize with a session and with a
connection code, token, refresh, rotation, revoke), refusals (wrong
verifier, reused code, unknown redirect, expired code, wrong connection
code throttled), metadata and `iss`, the internet gate; the endpoint's
JSON-RPC (initialize, tools, prompts, errors, `401` with the pointer);
proposals made through it; the official MCP client over streamable HTTP
against a running server.

## Docs

The guide's AI assistants page (English and Chinese): connecting from
claude.ai, the phone app and ChatGPT, and from Claude Code; Settings;
`mcp/README.md`; `deploy/RUNBOOK.md` (internet access makes connectors
possible); DEVELOPMENT.md.
