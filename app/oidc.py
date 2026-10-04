"""Signing in with a Google or Microsoft account (OpenID Connect).

A lab member can sign in with the Google or Microsoft account they already
use, instead of a BioManager password. Nothing about who may use the app
changes: a new account still waits for an admin's approval, and a disabled
one stays out.

The flow is the OpenID Connect authorization-code flow:

1. /auth/<provider>/start sends the browser to the provider with a random
   `state` (the answer must come back to the browser that asked), a `nonce`
   (the ID token must have been issued for this sign-in) and a PKCE
   challenge (a stolen authorization code is useless without the verifier,
   which never leaves this server's session).
2. /auth/<provider>/callback swaps the code for an ID token, server to
   server, and checks the token's signature against the provider's
   published keys, its audience (this app), issuer, expiry and nonce.
3. The account is looked up by issuer and subject: the provider's
   permanent identifier for that person. Never by email: an email address
   can change hands, and matching on it lets whoever holds an address
   sign in as someone else.

An account the app has not seen before becomes a sign-up request, as if
the person had registered. An existing BioManager user connects a Google
or Microsoft account from Settings while signed in.

Settings (a provider is offered only when both of its values are set):

  BIOMANAGER_GOOGLE_CLIENT_ID, BIOMANAGER_GOOGLE_CLIENT_SECRET
  BIOMANAGER_MICROSOFT_CLIENT_ID, BIOMANAGER_MICROSOFT_CLIENT_SECRET
  BIOMANAGER_MICROSOFT_TENANT   common (default: work, school and personal
                                accounts) | organizations | consumers | a
                                tenant ID, to accept one organisation only

Your institution's own sign-in, through any OpenID Connect provider (Okta,
Keycloak, Azure AD, Google Workspace, Shibboleth with its OIDC plugin):

  BIOMANAGER_OIDC_ISSUER        https://login.example.edu (its discovery
                                document is <issuer>/.well-known/openid-configuration)
  BIOMANAGER_OIDC_CLIENT_ID, BIOMANAGER_OIDC_CLIENT_SECRET
  BIOMANAGER_OIDC_NAME          what the button says, e.g. "CampusKey"

Universities in InCommon or eduGAIN (SAML) through CILogon, which turns
their sign-in into OpenID Connect (free for research; cilogon.org/oauth2/register):

  BIOMANAGER_CILOGON_CLIENT_ID, BIOMANAGER_CILOGON_CLIENT_SECRET
  BIOMANAGER_CILOGON_IDP        optional: the university's entityID, to skip
                                CILogon's list of institutions

Register this redirect URI with the provider (BIOMANAGER_BASE_URL, or the
address the request came to):

  https://<server>/auth/google/callback
  https://<server>/auth/microsoft/callback
  https://<server>/auth/institution/callback
  https://<server>/auth/cilogon/callback
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import jwt
from flask import Blueprint, abort, flash, g, redirect, request, session, url_for
from sqlalchemy import func, select

from . import notify, security
from .db import SessionLocal
from .i18n import gettext, translate_value
from .models import UserAccount, UserIdentity

bp = Blueprint("oidc", __name__, url_prefix="/auth")
log = logging.getLogger("biomanager.oidc")

LABELS = {"google": "Google", "microsoft": "Microsoft", "institution": "Your institution",
          "cilogon": "Your university (CILogon)"}
SIGN_IN_WINDOW = 10 * 60      # seconds between starting and finishing a sign-in
DISCOVERY_TTL = 24 * 3600
HTTP_TIMEOUT = 10


class SignInError(Exception):
    """A sign-in that must not proceed; the message is for the log."""


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    discovery_url: str
    client_id: str
    client_secret: str
    # Added to the authorization request: Google and Microsoft let the person
    # pick an account; CILogon can be sent straight to one university.
    extra: tuple = (("prompt", "select_account"),)


def providers() -> dict[str, Provider]:
    """The providers this server is configured for, in display order."""
    found: dict[str, Provider] = {}
    gid = os.environ.get("BIOMANAGER_GOOGLE_CLIENT_ID", "").strip()
    gsecret = os.environ.get("BIOMANAGER_GOOGLE_CLIENT_SECRET", "").strip()
    if gid and gsecret:
        found["google"] = Provider("google", LABELS["google"],
                                   "https://accounts.google.com/.well-known/openid-configuration", gid, gsecret)
    mid = os.environ.get("BIOMANAGER_MICROSOFT_CLIENT_ID", "").strip()
    msecret = os.environ.get("BIOMANAGER_MICROSOFT_CLIENT_SECRET", "").strip()
    tenant = os.environ.get("BIOMANAGER_MICROSOFT_TENANT", "").strip() or "common"
    if not re.fullmatch(r"[A-Za-z0-9.-]+", tenant):
        raise RuntimeError("BIOMANAGER_MICROSOFT_TENANT must be common, organizations, consumers or a tenant ID")
    if mid and msecret:
        found["microsoft"] = Provider(
            "microsoft", LABELS["microsoft"],
            f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration", mid, msecret)
    issuer = os.environ.get("BIOMANAGER_OIDC_ISSUER", "").strip().rstrip("/")
    iid = os.environ.get("BIOMANAGER_OIDC_CLIENT_ID", "").strip()
    isecret = os.environ.get("BIOMANAGER_OIDC_CLIENT_SECRET", "").strip()
    if issuer and iid and isecret:
        if not issuer.startswith("https://"):
            raise RuntimeError("BIOMANAGER_OIDC_ISSUER must be an https:// address")
        name = os.environ.get("BIOMANAGER_OIDC_NAME", "").strip()[:40] or LABELS["institution"]
        found["institution"] = Provider("institution", name, f"{issuer}/.well-known/openid-configuration",
                                        iid, isecret, extra=())
    cid = os.environ.get("BIOMANAGER_CILOGON_CLIENT_ID", "").strip()
    csecret = os.environ.get("BIOMANAGER_CILOGON_CLIENT_SECRET", "").strip()
    if cid and csecret:
        idp = os.environ.get("BIOMANAGER_CILOGON_IDP", "").strip()
        found["cilogon"] = Provider("cilogon", LABELS["cilogon"], "https://cilogon.org/.well-known/openid-configuration",
                                    cid, csecret, extra=(("idphint", idp),) if idp else ())
    return found


def provider_choices() -> list[dict]:
    """For templates: [{key, label}] of the configured providers (a built-in
    label in the page's language; a name the server set stays as set)."""
    return [{"key": p.key, "label": translate_value(p.label)} for p in providers().values()]


def _shown(provider: Provider) -> str:
    """The provider's name in a message: "Your institution" translated, a
    name the server was given as it is."""
    return translate_value(provider.label)


# ---------------------------------------------------------------- talking to the provider

def _https_only(url: str) -> str:
    if urllib.parse.urlparse(url).scheme != "https":
        raise SignInError(f"refusing a non-HTTPS provider URL: {url}")
    return url


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(_https_only(url), timeout=HTTP_TIMEOUT) as response:
        return json.load(response)


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(_https_only(url), data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode(errors="replace")
        raise SignInError(f"token endpoint said {exc.code}: {detail}") from None


_discovery: dict[str, tuple[float, dict]] = {}
_jwks_clients: dict[str, jwt.PyJWKClient] = {}
_cache_lock = threading.Lock()


def discovery(provider: Provider) -> dict:
    with _cache_lock:
        cached = _discovery.get(provider.discovery_url)
        if cached and time.time() - cached[0] < DISCOVERY_TTL:
            return cached[1]
    conf = _get_json(provider.discovery_url)
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not conf.get(key):
            raise SignInError(f"{provider.label} discovery document has no {key}")
    with _cache_lock:
        _discovery[provider.discovery_url] = (time.time(), conf)
    return conf


def _jwks_client(uri: str) -> jwt.PyJWKClient:
    with _cache_lock:
        if uri not in _jwks_clients:
            _jwks_clients[uri] = jwt.PyJWKClient(_https_only(uri), cache_keys=True, lifespan=3600)
        return _jwks_clients[uri]


def signing_key(conf: dict, id_token: str):
    return _jwks_client(conf["jwks_uri"]).get_signing_key_from_jwt(id_token).key


def verify_id_token(provider: Provider, conf: dict, id_token: str, nonce: str) -> dict:
    """The token's claims, once everything about it has been checked."""
    try:
        claims = jwt.decode(
            id_token, signing_key(conf, id_token),
            algorithms=["RS256"],        # never "none", never a shared-secret algorithm
            audience=provider.client_id,
            leeway=60,
            options={"require": ["iss", "sub", "aud", "exp", "iat"], "verify_iss": False},
        )
    except jwt.PyJWTError as exc:
        raise SignInError(f"ID token rejected: {exc}") from None

    # Microsoft's multi-tenant issuer is a template: {tenantid} stands for
    # the tenant in the token's own tid claim.
    expected = conf["issuer"].replace("{tenantid}", str(claims.get("tid", "")))
    issuer = claims["iss"]
    if provider.key == "google" and issuer == "accounts.google.com":
        issuer = "https://accounts.google.com"
    if issuer != expected:
        raise SignInError(f"ID token issuer {claims['iss']!r} is not {expected!r}")
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if len(audiences) > 1 and claims.get("azp") != provider.client_id:
        raise SignInError("ID token issued to several audiences, and not authorised for this app")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SignInError("ID token nonce does not match this sign-in")
    claims["iss"] = issuer
    return claims


# ---------------------------------------------------------------- routes

def _provider_or_404(key: str) -> Provider:
    provider = providers().get(key)
    if provider is None:
        abort(404)
    return provider


def redirect_uri(provider: Provider) -> str:
    base = os.environ.get("BIOMANAGER_BASE_URL", "").strip() or request.url_root
    return f"{base.rstrip('/')}/auth/{provider.key}/callback"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@bp.route("/<key>/start")
def start(key: str):
    provider = _provider_or_404(key)
    linking = request.args.get("link") == "1"
    if linking and g.get("user") is None:
        return redirect(url_for("login"))
    if security.https_required_but_missing():
        flash(gettext("This server only accepts sign-ins over HTTPS. Open it with an https:// address."), "error")
        return redirect(url_for("login"))
    try:
        conf = discovery(provider)
    except (SignInError, OSError, ValueError) as exc:
        log.warning("%s sign-in unavailable: %s", provider.label, exc)
        flash(gettext("%(provider)s sign-in is not reachable right now. Try again, or use your password.",
                      provider=_shown(provider)), "error")
        return redirect(url_for("settings" if linking else "login"))

    verifier = secrets.token_urlsafe(64)
    pending = {
        "provider": provider.key,
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": verifier,
        "next": security.safe_next(request.args.get("next")) or "",
        "link": linking,
        "started": time.time(),
    }
    session["oidc"] = pending
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri(provider),
        "state": pending["state"],
        "nonce": pending["nonce"],
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        **dict(provider.extra),
    }
    return redirect(conf["authorization_endpoint"] + "?" + urllib.parse.urlencode(params))


@bp.route("/<key>/callback")
def callback(key: str):
    provider = _provider_or_404(key)
    pending = session.pop("oidc", None) or {}
    back = url_for("settings") if pending.get("link") else url_for("login")

    if request.args.get("error"):
        flash(gettext("Signing in with %(provider)s was cancelled.", provider=_shown(provider)), "error")
        return redirect(back)
    fresh = time.time() - float(pending.get("started", 0)) < SIGN_IN_WINDOW
    if (pending.get("provider") != provider.key or not fresh
            or not hmac.compare_digest(request.args.get("state", ""), str(pending.get("state", "")))):
        flash(gettext("That %(provider)s sign-in expired or was not started here. Try again.",
                      provider=_shown(provider)), "error")
        return redirect(back)

    try:
        conf = discovery(provider)
        tokens = _post_form(conf["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": request.args.get("code", ""),
            "redirect_uri": redirect_uri(provider),
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code_verifier": pending["verifier"],
        })
        if not tokens.get("id_token"):
            raise SignInError("token response has no id_token")
        claims = verify_id_token(provider, conf, tokens["id_token"], pending["nonce"])
    except (SignInError, OSError, ValueError, KeyError) as exc:
        log.warning("%s sign-in refused: %s", provider.label, exc)
        flash(gettext("Signing in with %(provider)s did not work. Try again, or use your password.",
                      provider=_shown(provider)), "error")
        return redirect(back)

    if pending.get("link"):
        return _connect(provider, claims)
    return _sign_in(provider, claims, pending.get("next") or "")


def _email(claims: dict) -> str:
    """The address to show, only when the provider vouches for it."""
    email = str(claims.get("email") or "").strip()
    if claims.get("email_verified") is False:
        return ""
    return email[:200]


def _identity(db_session, claims: dict) -> UserIdentity | None:
    return db_session.scalar(select(UserIdentity).where(
        UserIdentity.issuer == claims["iss"], UserIdentity.subject == str(claims["sub"])))


def _connect(provider: Provider, claims: dict):
    """Settings → Connect: attach this provider account to the signed-in user."""
    if g.get("user") is None:
        return redirect(url_for("login"))
    with SessionLocal() as db_session:
        existing = _identity(db_session, claims)
        if existing is not None and existing.user_id_fk != g.user.id:
            flash(gettext("That %(provider)s account is already connected to another BioManager account.",
                          provider=_shown(provider)), "error")
        elif existing is not None:
            flash(gettext("That %(provider)s account was already connected.", provider=_shown(provider)), "success")
        else:
            db_session.add(UserIdentity(user_id_fk=g.user.id, provider=provider.key, issuer=claims["iss"],
                                        subject=str(claims["sub"]), email=_email(claims)))
            db_session.commit()
            flash(gettext("%(provider)s account connected. You can sign in with it from now on.",
                          provider=_shown(provider)), "success")
    return redirect(url_for("settings"))


def _sign_in(provider: Provider, claims: dict, next_url: str):
    from .app import landing_url  # the app module is fully loaded by the time anyone signs in

    with SessionLocal() as db_session:
        identity = _identity(db_session, claims)
        if identity is not None:
            user = db_session.get(UserAccount, identity.user_id_fk)
            if user is None:
                flash(gettext("That account no longer exists."), "error")
                return redirect(url_for("login"))
            if user.role == "pending":
                flash(gettext("Your account is waiting for a lab admin to approve it."), "error")
                return redirect(url_for("login"))
            if user.disabled:
                flash(gettext("This account is disabled. Contact an admin."), "error")
                return redirect(url_for("login"))
            identity.last_login_at = datetime.utcnow()
            identity.email = _email(claims) or identity.email
            db_session.commit()
            security.start_session(user)
            flash(gettext("Welcome, %(name)s.", name=user.display_name or user.username), "success")
            return redirect(next_url or landing_url(user))
        return _request_account(db_session, provider, claims)


def _unique_username(db_session, claims: dict) -> str:
    source = (str(claims.get("email") or "").split("@")[0] or str(claims.get("name") or "") or "member")
    base = re.sub(r"[^a-z0-9._-]+", "", source.lower().replace(" ", "."))[:30].strip("._-") or "member"
    candidate, n = base, 1
    while db_session.scalar(select(UserAccount.id).where(func.lower(UserAccount.username) == candidate)):
        n += 1
        candidate = f"{base[:27]}{n}"
    return candidate


def _request_account(db_session, provider: Provider, claims: dict):
    """A provider account nobody has seen: a sign-up waiting for approval."""
    from .services import add_notification

    if db_session.scalar(select(func.count(UserAccount.id))) == 0:
        flash(gettext("Create the admin account first (it needs the setup code). Then connect %(provider)s in Settings.",
                      provider=_shown(provider)), "error")
        return redirect(url_for("register"))
    username = _unique_username(db_session, claims)
    display_name = str(claims.get("name") or "").strip()[:120]
    user = UserAccount(username=username, display_name=display_name, email=_email(claims),
                       password_hash=security.NO_PASSWORD, role="pending", disabled=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(UserIdentity(user_id_fk=user.id, provider=provider.key, issuer=claims["iss"],
                                subject=str(claims["sub"]), email=_email(claims)))
    admins = db_session.scalars(select(UserAccount.username).where(
        UserAccount.role == "admin", UserAccount.disabled.is_(False))).all()
    for admin_name in admins:
        add_notification(db_session, admin_name, notify.SIGNUP_TITLE, notify.SIGNUP_WITH_PROVIDER,
                         category="account", link=url_for("admin_users"), message_values={
                             "who": display_name or username, "provider": provider.label, "username": username})
    db_session.commit()
    flash(gettext("Account requested as %(username)s. A lab admin needs to approve it before you can sign in.",
                  username=username), "success")
    return redirect(url_for("login"))


@bp.route("/identities/<int:identity_id>/disconnect", methods=["POST"])
def disconnect(identity_id: int):
    if g.get("user") is None:
        return redirect(url_for("login"))
    with SessionLocal() as db_session:
        identity = db_session.get(UserIdentity, identity_id)
        if identity is None or identity.user_id_fk != g.user.id:
            abort(404)
        user = db_session.get(UserAccount, g.user.id)
        others = db_session.scalar(select(func.count(UserIdentity.id)).where(
            UserIdentity.user_id_fk == user.id, UserIdentity.id != identity.id))
        label = LABELS.get(identity.provider, identity.provider.title())
        if not others and not security.has_password(user):
            flash(gettext("Set a password first: without %(provider)s you would have no way to sign in.",
                          provider=translate_value(label)), "error")
        else:
            db_session.delete(identity)
            db_session.commit()
            flash(gettext("%(provider)s account disconnected.", provider=translate_value(label)), "success")
    return redirect(url_for("settings"))
