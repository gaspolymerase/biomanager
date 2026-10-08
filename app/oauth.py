"""Signing an assistant app in (OAuth 2.1), so claude.ai, Claude's apps and
ChatGPT can use BioManager as a connector (app/mcp_http.py).

What those apps need, and what this does (docs/plans/remote-connector.md):

* Discovery: /.well-known/oauth-protected-resource (RFC 9728; also under
  /api/v1/mcp) names this server as the authorization server, whose
  metadata is /.well-known/oauth-authorization-server (RFC 8414). Every 401
  from /api/v1 points at the first (app/api.py). The issuer is the address
  the request came in on, the same string everywhere, and comes back as
  `iss` with every authorization answer (RFC 9207).
* POST /oauth/register (RFC 7591): an app registers itself, with its own
  redirect addresses: any https one, or a loopback one (Claude Code),
  matched without its port.
* /oauth/authorize: PKCE S256 required. Someone signed in (on the lab's
  network) sees what is asking and where they will be sent back, and says
  yes or no. From the internet the lab never shows its sign-in form, so the
  page asks instead for a connection code the person made under Settings →
  API tokens → Connect an AI assistant (once, for ten minutes).
* POST /oauth/token: a code (once, five minutes, the verifier checked)
  gives an access token, which is a Read and propose api_tokens row named
  after the app, for an hour, and a refresh token for 90 days. Refreshing
  changes both, keeping one row per connection in Settings; revoking that
  row ends the connection.

An assistant signed in this way can do exactly what a Read and propose
token can: read, and propose changes the person approves in BioManager.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import delete, select

from . import security
from .db import SessionLocal
from .i18n import gettext
from .models import ApiToken, OAuthClient, OAuthCode, OAuthGrant, OAuthLinkCode, UserAccount

bp = Blueprint("oauth", __name__)

SCOPE = "propose"
CODE_FOR = timedelta(minutes=5)
LINK_CODE_FOR = timedelta(minutes=10)
ACCESS_FOR = timedelta(hours=1)
REFRESH_FOR = timedelta(days=90)
UNUSED_CLIENT_FOR = timedelta(days=30)
MAX_REDIRECTS = 10
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
LINK_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

# Wrong connection codes from anywhere, counted together (as guest codes are).
link_throttle = security.LoginThrottle(limit=30, window=15 * 60)
LINK_THROTTLE_KEY = ("assistant-link-code",)
register_throttle = security.LoginThrottle(limit=60, window=60 * 60)


def _hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def issuer() -> str:
    """This server's address as the request reached it (the public one from
    the internet, the lab's own from inside)."""
    return request.url_root.rstrip("/")


def resource() -> str:
    return issuer() + "/api/v1/mcp"


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.after_request
def _not_cached(response):
    return _no_store(response)


# ---------------------------------------------------------------- discovery

@bp.get("/.well-known/oauth-protected-resource")
@bp.get("/.well-known/oauth-protected-resource/api/v1/mcp")
def protected_resource():
    return jsonify({"resource": resource(), "authorization_servers": [issuer()], "scopes_supported": [SCOPE],
                    "bearer_methods_supported": ["header"], "resource_name": "BioManager"})


@bp.get("/.well-known/oauth-authorization-server")
def authorization_server():
    base = issuer()
    return jsonify({
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": [SCOPE],
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": "https://biomanager.org/guide/ai-assistants",
    })


# ---------------------------------------------------------------- registration (RFC 7591)

def _redirect_ok(uri: str) -> bool:
    try:
        parts = urlparse(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.netloc:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and (parts.hostname or "") in {"localhost", "127.0.0.1", "::1"}


def _reg_error(code: str, description: str):
    return jsonify({"error": code, "error_description": description}), 400


@bp.post("/oauth/register")
def register():
    if register_throttle.retry_after(("oauth-register",)):
        return jsonify({"error": "slow_down", "error_description": "Too many registrations; try again later."}), 429
    register_throttle.failed(("oauth-register",))     # counts every registration, not only failures
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _reg_error("invalid_client_metadata", "Send the client's metadata as JSON.")
    uris = data.get("redirect_uris")
    if (not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECTS
            or not all(isinstance(u, str) and len(u) < 500 and _redirect_ok(u) for u in uris)):
        return _reg_error("invalid_redirect_uri", "redirect_uris: one or more https addresses, or a loopback one.")
    method = data.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return _reg_error("invalid_client_metadata", "token_endpoint_auth_method: none, client_secret_post or "
                                                     "client_secret_basic.")
    name = " ".join(str(data.get("client_name") or "").split())[:80] or "AI assistant"
    client_id = "bmc_" + secrets.token_urlsafe(18)
    secret = "bms_" + secrets.token_urlsafe(32) if method != "none" else ""
    now = datetime.utcnow()
    with SessionLocal() as s:
        # Clients nobody has used for a while go, unless a connection made
        # with one can still be renewed.
        live = select(OAuthGrant.client_id).where(OAuthGrant.refresh_expires_at > now)
        s.execute(delete(OAuthClient).where(OAuthClient.created_at < now - UNUSED_CLIENT_FOR,
                                            (OAuthClient.last_used_at.is_(None))
                                            | (OAuthClient.last_used_at < now - UNUSED_CLIENT_FOR),
                                            OAuthClient.client_id.not_in(live)))
        s.add(OAuthClient(client_id=client_id, secret_hash=_hash(secret) if secret else "", name=name,
                          redirect_uris=json.dumps(uris), created_at=now))
        s.commit()
    out = {"client_id": client_id, "client_id_issued_at": int(now.timestamp()), "client_name": name,
           "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "token_endpoint_auth_method": method, "scope": SCOPE}
    if secret:
        out.update(client_secret=secret, client_secret_expires_at=0)
    return jsonify(out), 201


def _client(session, client_id: str) -> OAuthClient | None:
    return session.scalar(select(OAuthClient).where(OAuthClient.client_id == (client_id or "")))


def redirect_matches(registered: list[str], given: str) -> bool:
    """Exactly one of the client's own, or, for a loopback address (Claude
    Code's), the same but on any port (RFC 8252)."""
    if given in registered:
        return True
    try:
        g_parts = urlparse(given)
    except ValueError:
        return False
    if g_parts.scheme != "http" or (g_parts.hostname or "") not in {"localhost", "127.0.0.1", "::1"}:
        return False
    for uri in registered:
        r = urlparse(uri)
        if (r.scheme, r.hostname, r.path, r.query) == ("http", g_parts.hostname, g_parts.path, g_parts.query):
            return True
    return False


# ---------------------------------------------------------------- authorize

def _back(redirect_uri: str, **params):
    """To the app, with `iss` (RFC 9207) on every answer."""
    params = {k: v for k, v in params.items() if v not in (None, "")}
    params["iss"] = issuer()
    sep = "&" if urlparse(redirect_uri).query else "?"
    return redirect(redirect_uri + sep + urlencode(params), code=302)


def _request_args() -> dict:
    src = request.form if request.method == "POST" else request.args
    return {k: (src.get(k) or "").strip() for k in ("response_type", "client_id", "redirect_uri", "state",
                                                      "code_challenge", "code_challenge_method", "scope",
                                                      "resource")}


def _refuse_page(message: str, status: int = 400):
    return render_template("oauth/authorize.html", problem=message, args={}, client=None), status


@bp.route("/oauth/authorize", methods=["GET", "POST"])
def authorize():
    from .guests import from_internet
    args = _request_args()
    with SessionLocal() as s:
        client = _client(s, args["client_id"])
        if client is None:
            return _refuse_page(gettext("This assistant app isn't registered here any more. Start connecting it again."))
        uris = json.loads(client.redirect_uris or "[]")
        if not redirect_matches(uris, args["redirect_uri"]):
            return _refuse_page(gettext("The address this app wants to send you back to isn't one it registered."))
        if args["response_type"] != "code":
            return _back(args["redirect_uri"], error="unsupported_response_type", state=args["state"])
        if not args["code_challenge"] or args["code_challenge_method"] != "S256":
            return _back(args["redirect_uri"], error="invalid_request", state=args["state"],
                         error_description="PKCE with S256 is required.")
        if args["resource"] and args["resource"].rstrip("/") not in (resource(), issuer()):
            return _back(args["redirect_uri"], error="invalid_target", state=args["state"])
        user = g.get("user")
        if user is None and not from_internet():
            # On the lab's network: sign in as usual, then come back here.
            return redirect(url_for("login", next=request.full_path))
        back_to = urlparse(args["redirect_uri"])
        context = {"client": client, "args": args, "host": back_to.hostname or "",
                   "loopback": (back_to.hostname or "") in {"localhost", "127.0.0.1", "::1"},
                   "need_code": user is None, "problem": ""}
        if request.method == "GET":
            return render_template("oauth/authorize.html", **context)
        if request.form.get("decision") != "allow":
            return _back(args["redirect_uri"], error="access_denied", state=args["state"])
        if user is None:
            user = _redeem_link_code(s, request.form.get("link_code", ""))
            if user is None:
                context["problem"] = gettext(
                    "That connection code isn't right, or it has run out. Make a new one under Settings → AI assistant & tokens → Connect an AI assistant.")
                return render_template("oauth/authorize.html", **context), 400
        from .api import may_make_tokens
        user = s.get(UserAccount, user.id)
        if not may_make_tokens(s, user):
            return _back(args["redirect_uri"], error="access_denied", state=args["state"],
                         error_description="This lab keeps tokens to its admins.")
        code = secrets.token_urlsafe(32)
        s.add(OAuthCode(code_hash=_hash(code), client_id=client.client_id, user_id_fk=user.id,
                        redirect_uri=args["redirect_uri"], code_challenge=args["code_challenge"], scope=SCOPE,
                        expires_at=datetime.utcnow() + CODE_FOR))
        client.last_used_at = datetime.utcnow()
        s.commit()
        return _back(args["redirect_uri"], code=code, state=args["state"])


def normalise_link_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def new_link_code(session, user) -> str:
    raw = "".join(secrets.choice(LINK_ALPHABET) for _ in range(12))
    session.add(OAuthLinkCode(code_hash=_hash(raw), user_id_fk=user.id,
                              expires_at=datetime.utcnow() + LINK_CODE_FOR))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def _redeem_link_code(session, typed: str):
    if link_throttle.retry_after(LINK_THROTTLE_KEY):
        return None
    now = datetime.utcnow()
    row = session.scalar(select(OAuthLinkCode).where(OAuthLinkCode.code_hash == _hash(normalise_link_code(typed))))
    if row is None or row.used_at is not None or row.expires_at <= now:
        link_throttle.failed(LINK_THROTTLE_KEY)
        return None
    row.used_at = now
    user = session.get(UserAccount, row.user_id_fk)
    if user is None or user.disabled:
        return None
    return user


# ---------------------------------------------------------------- token

def _token_error(code: str, description: str = "", status: int = 400):
    return _no_store(jsonify({"error": code, **({"error_description": description} if description else {})})), status


def _client_auth(session):
    """(client, None) or (None, error response): the client's id, and its
    secret if it has one, from the form or HTTP Basic."""
    client_id, secret = request.form.get("client_id", ""), request.form.get("client_secret", "")
    auth = request.authorization
    if auth is not None and auth.type == "basic":
        client_id, secret = auth.username or client_id, auth.password or ""
    client = _client(session, client_id)
    if client is None:
        return None, _token_error("invalid_client", "Unknown client.", 401)
    if client.secret_hash and not secrets.compare_digest(client.secret_hash, _hash(secret)):
        return None, _token_error("invalid_client", "Wrong client secret.", 401)
    return client, None


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verifier or ""):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return secrets.compare_digest(digest, challenge)


def _issue(session, client: OAuthClient, user_id: int, grant: OAuthGrant | None = None):
    """A new access token and refresh token for this connection."""
    from .api import TOKEN_PREFIX, new_token, token_hash
    now = datetime.utcnow()
    raw_access, raw_refresh = new_token(), "bmr_" + secrets.token_urlsafe(32)
    if grant is None:
        tok = ApiToken(user_id_fk=user_id, label=client.name[:80], token_hash=token_hash(raw_access),
                       hint=raw_access[:10], scope=SCOPE, expires_at=now + ACCESS_FOR)
        session.add(tok)
        session.flush()
        grant = OAuthGrant(client_id=client.client_id, user_id_fk=user_id, api_token_id_fk=tok.id,
                           refresh_hash=_hash(raw_refresh), refresh_expires_at=now + REFRESH_FOR)
        session.add(grant)
    else:
        tok = session.get(ApiToken, grant.api_token_id_fk)
        tok.token_hash, tok.hint, tok.expires_at = token_hash(raw_access), raw_access[:10], now + ACCESS_FOR
        grant.refresh_hash, grant.refresh_expires_at = _hash(raw_refresh), now + REFRESH_FOR
    assert raw_access.startswith(TOKEN_PREFIX)
    client.last_used_at = now
    session.commit()
    return _no_store(jsonify({"access_token": raw_access, "token_type": "Bearer",
                              "expires_in": int(ACCESS_FOR.total_seconds()), "refresh_token": raw_refresh,
                              "scope": SCOPE}))


@bp.post("/oauth/token")
def token():
    grant_type = request.form.get("grant_type", "")
    with SessionLocal() as s:
        client, refused = _client_auth(s)
        if refused:
            return refused
        now = datetime.utcnow()
        if grant_type == "authorization_code":
            row = s.scalar(select(OAuthCode).where(OAuthCode.code_hash == _hash(request.form.get("code", ""))))
            if (row is None or row.client_id != client.client_id or row.used_at is not None
                    or row.expires_at <= now):
                return _token_error("invalid_grant", "The code is unknown, used or expired.")
            row.used_at = now
            s.commit()
            given = request.form.get("redirect_uri", "")
            if given and given != row.redirect_uri:
                return _token_error("invalid_grant", "redirect_uri differs from the authorization request.")
            if not _pkce_ok(request.form.get("code_verifier", ""), row.code_challenge):
                return _token_error("invalid_grant", "The code_verifier doesn't match.")
            user = s.get(UserAccount, row.user_id_fk)
            if user is None or user.disabled:
                return _token_error("invalid_grant", "That account can't connect.")
            return _issue(s, client, user.id)
        if grant_type == "refresh_token":
            grant = s.scalar(select(OAuthGrant).where(
                OAuthGrant.refresh_hash == _hash(request.form.get("refresh_token", ""))))
            if grant is None or grant.client_id != client.client_id or grant.refresh_expires_at <= now:
                return _token_error("invalid_grant", "The refresh token is unknown or expired.")
            tok = s.get(ApiToken, grant.api_token_id_fk)
            user = s.get(UserAccount, grant.user_id_fk)
            if tok is None or tok.revoked_at is not None or user is None or user.disabled:
                return _token_error("invalid_grant", "This connection was ended in BioManager.")
            return _issue(s, client, user.id, grant)
        return _token_error("unsupported_grant_type", "authorization_code or refresh_token.")


# ---------------------------------------------------------------- Settings: Connect an AI assistant

def _signed_in():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


@bp.get("/settings/assistant")
def connect_page():
    """Settings → AI assistant & tokens → Connect an AI assistant: the ways in."""
    blocked = _signed_in()
    if blocked:
        return blocked
    from .api import may_make_tokens
    from .guests import public_url
    with SessionLocal() as s:
        allowed = may_make_tokens(s, s.get(UserAccount, g.user.id))
    public = public_url()
    return render_template("oauth/connect.html", allowed=allowed, public=public,
                           connector_url=(public + "/api/v1/mcp") if public else "",
                           local_url=issuer() + "/api/v1/mcp", code=None)


@bp.post("/settings/assistant/code")
def make_link_code():
    blocked = _signed_in()
    if blocked:
        return blocked
    from .api import may_make_tokens
    from .guests import public_url
    with SessionLocal() as s:
        user = s.get(UserAccount, g.user.id)
        if not may_make_tokens(s, user):
            abort(403)
        s.execute(delete(OAuthLinkCode).where(OAuthLinkCode.expires_at < datetime.utcnow() - timedelta(days=1)))
        code = new_link_code(s, user)
        s.commit()
    public = public_url()
    return render_template("oauth/connect.html", allowed=True, public=public,
                           connector_url=(public + "/api/v1/mcp") if public else "",
                           local_url=issuer() + "/api/v1/mcp", code=code)
