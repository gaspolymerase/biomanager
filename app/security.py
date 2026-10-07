"""What it takes to put BioManager on a network other people can reach.

The app started life on one laptop, where nobody else can send it a
request. On a lab server anyone on the network can, so the decisions about
which requests to trust live here, in one place:

- **The signing key.** Session cookies are signed with SECRET_KEY. Without
  one set, a random key is made once and kept in the data folder (owner-only),
  so no two installs share a key and nobody can forge a login cookie.
- **Cross-site requests.** A change (any POST) is refused when the browser
  says it came from another site: `Sec-Fetch-Site` on modern browsers, the
  `Origin`/`Referer` host otherwise. This is the check Go's net/http ships
  as CrossOriginProtection; it needs no token in every form, so every form
  and every autosave is covered, including ones written later. Requests
  without any of those headers are not from a browser and are let through.
- **Cookies.** HttpOnly, SameSite=Lax, Secure when served over HTTPS, and a
  rolling lifetime. A session is bound to the password it was made with,
  so changing or resetting a password signs out every other session.
- **Scripts.** A Content-Security-Policy lets the browser run only the
  app's own script files and inline scripts carrying this request's nonce,
  so injected markup cannot run script even if it gets onto a page. Pages
  use data-on-click=… (static/actions.js) instead of inline onclick.
  BIOMANAGER_CSP=report-only logs what would be blocked without blocking.
- **Uploads.** Only signed-in users can fetch them, and they are served
  so that an uploaded HTML or SVG file cannot run script as the app.
- **Sign-in.** Failed attempts are rate-limited per address and per
  username, and passwords must be at least MIN_PASSWORD_LENGTH characters.
- **Tokens at rest.** Google Calendar tokens are encrypted in the database
  with a key derived from the signing key (EncryptedText in models.py).
- **The first account.** The first person to register becomes admin, so on
  a server that needs the setup code the server prints at start-up; the
  desktop app, which only listens on this machine, does not ask for it.

Settings, all optional:

  BIOMANAGER_ENV=production     set by wsgi.py; stricter defaults
  BIOMANAGER_HTTPS=1|0          Secure cookies (default: on in production)
  BIOMANAGER_PROXY_HOPS=1       behind nginx/Caddy: trust its X-Forwarded-*
  BIOMANAGER_TRUSTED_ORIGINS    other origins allowed to post, comma separated
  BIOMANAGER_SESSION_DAYS=7     idle days before a sign-in expires
  BIOMANAGER_MAX_UPLOAD_MB=64   largest request body accepted
  BIOMANAGER_CSP=enforce        enforce | report-only | off
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app, flash, g, jsonify, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from .i18n import gettext
from .paths import data_dir

log = logging.getLogger("biomanager.security")

DEV_SECRET = "dev-only-change-me"
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 12
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
UPLOADS_PREFIX = "/static/uploads/"
# Uploads a browser may show in the page; everything else is downloaded.
INLINE_UPLOADS = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf"}


def production() -> bool:
    return os.environ.get("BIOMANAGER_ENV", "").strip().lower() == "production"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number") from None


def _read_or_create(path: Path, make) -> str:
    """The contents of `path`, creating it (readable by its owner only) on
    first use. Several worker processes may start at once: the file is
    written in full under a temporary name and linked into place, so the
    loser of the race reads the winner's file, never a half-written one."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(make() + "\n")
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------- signing key

def secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key == DEV_SECRET:
        log.warning("SECRET_KEY is the published development value; ignoring it.")
        key = ""
    if key:
        if production() and len(key) < MIN_SECRET_LENGTH:
            raise RuntimeError(
                f"SECRET_KEY is too short for a shared server (use {MIN_SECRET_LENGTH}+ random "
                "characters: python -c 'import secrets; print(secrets.token_urlsafe(48))'), "
                "or unset it to use the key kept in the data folder.")
        return key
    return _read_or_create(data_dir() / "secret_key", lambda: secrets.token_urlsafe(48))


# ---------------------------------------------------------------- secrets at rest

_ENCRYPTED_PREFIX = "enc:v1:"
_fernet_cache: list = []


def _fernet():
    """A Fernet key derived from the signing key, so there is one secret to
    keep (and back up) rather than two. Changing SECRET_KEY, or losing the
    data folder's secret_key file, makes stored tokens unreadable: people
    then reconnect Google Calendar, nothing else is lost."""
    if not _fernet_cache:
        from base64 import urlsafe_b64encode
        from cryptography.fernet import Fernet

        digest = sha256(b"biomanager tokens at rest\0" + secret_key().encode()).digest()
        _fernet_cache.append(Fernet(urlsafe_b64encode(digest)))
    return _fernet_cache[0]


def encrypt_text(value: str | None) -> str | None:
    if not value or value.startswith(_ENCRYPTED_PREFIX):
        return value
    return _ENCRYPTED_PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str | None) -> str | None:
    """Plain text stored before encryption existed is returned as it is."""
    if not value or not value.startswith(_ENCRYPTED_PREFIX):
        return value
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value[len(_ENCRYPTED_PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("A stored token was encrypted with a different SECRET_KEY; treating it as missing.")
        return ""


GENERATION = "session_generation:{}"     # app_settings: bumped to end every session of an account
SIGNED_OUT = "signed_out:"               # app_settings: a session ended by Sign out, and when


def _generation(user, db=None) -> str:
    from .inventory_service import get_setting
    if db is not None:
        return get_setting(db, GENERATION.format(user.id), "")
    from .db import SessionLocal
    with SessionLocal() as s:
        return get_setting(s, GENERATION.format(user.id), "")


def session_stamp(user, db=None) -> str:
    """Ties a session to the password it was signed in with: the stored hash
    changes with every password change, so older sessions stop matching.
    Disabling an account moves its generation on (end_sessions), so
    enabling it again doesn't bring its old sessions back."""
    generation = _generation(user, db)
    material = user.password_hash + (f":{generation}" if generation else "")
    return hmac.new(current_app.secret_key.encode(), material.encode(), sha256).hexdigest()[:24]


def session_matches(stored: str | None, user, db=None) -> bool:
    return bool(stored) and hmac.compare_digest(stored, session_stamp(user, db))


def end_sessions(db, user) -> None:
    """Every session this account has, on every browser, stops working."""
    from .inventory_service import get_setting, set_setting
    key = GENERATION.format(user.id)
    current = get_setting(db, key, "0")
    set_setting(db, key, str(int(current) + 1 if current.isdigit() else 1))


def sign_out(db) -> None:
    """End this browser's session for good: a copy of its cookie (a shared
    computer, a stolen laptop) no longer signs anyone in. Kept as long as a
    session could last, then forgotten."""
    from flask import session
    from sqlalchemy import delete

    from .inventory_service import set_setting
    from .models import AppSetting
    sid = session.get("sid")
    now = datetime.utcnow()
    if sid:
        set_setting(db, SIGNED_OUT + sid, now.isoformat(timespec="seconds"))
    forget = (now - current_app.permanent_session_lifetime - timedelta(days=1)).isoformat(timespec="seconds")
    db.execute(delete(AppSetting).where(AppSetting.key.like(SIGNED_OUT + "%"), AppSetting.value < forget))
    db.commit()
    session.clear()


def signed_out(db) -> bool:
    """Was this session ended by Sign out? A session from before sessions
    had an id gets one now, so it can be."""
    from flask import session

    from .models import AppSetting
    sid = session.get("sid")
    if not sid:
        session["sid"] = secrets.token_urlsafe(18)
        return False
    return db.get(AppSetting, SIGNED_OUT + sid) is not None


# ---------------------------------------------------------------- set-up

from flask.sessions import SecureCookieSessionInterface  # noqa: E402


class _SessionInterface(SecureCookieSessionInterface):
    """The API is signed in by its token alone (app/api.py): its replies
    never set or refresh the browser's session cookie."""

    def should_set_cookie(self, app, session) -> bool:
        if request.path == "/api/v1" or request.path.startswith("/api/"):
            return False
        return super().should_set_cookie(app, session)


def init_app(app) -> None:
    """Configure cookies, limits and the request checks. Call after the
    hook that loads g.user, which the uploads check needs."""
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_flag("BIOMANAGER_HTTPS", production()),
        PERMANENT_SESSION_LIFETIME=timedelta(days=_int("BIOMANAGER_SESSION_DAYS", 7)),
        MAX_CONTENT_LENGTH=_int("BIOMANAGER_MAX_UPLOAD_MB", 64) * 1024 * 1024,
    )
    app.session_interface = _SessionInterface()
    hops = _int("BIOMANAGER_PROXY_HOPS", 0)
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops)
    app.before_request(refuse_cross_site)
    app.before_request(guard_uploads)
    app.after_request(add_security_headers)
    app.register_error_handler(413, too_large)
    app.jinja_env.globals["csp_nonce"] = csp_nonce
    app.add_url_rule(CSP_REPORT_PATH, "csp_report", csp_report, methods=["POST"])


# ---------------------------------------------------------------- cross-site requests

def _trusted_hosts() -> set[str]:
    raw = os.environ.get("BIOMANAGER_TRUSTED_ORIGINS", "")
    return {urlparse(o.strip()).netloc or o.strip() for o in raw.split(",") if o.strip()}


def cross_site_reason() -> str | None:
    """Why this request looks like it was sent by another site, or None."""
    if request.method in SAFE_METHODS or request.path == CSP_REPORT_PATH:
        return None
    if request.path in ("/oauth/token", "/oauth/register"):
        # Called by an assistant app's server with its own credentials, never a cookie (app/oauth.py).
        return None
    if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
        # Signed in by a bearer token only, which another site can't send
        # for you; the session cookie is never read there (app/api.py).
        return None
    origin = request.headers.get("Origin", "")
    if origin and origin != "null" and urlparse(origin).netloc in _trusted_hosts():
        return None
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site:
        # Only sent to HTTPS (or localhost) origins, and then by every
        # current browser; the header cannot be set by page script.
        return None if fetch_site in {"same-origin", "none"} else f"Sec-Fetch-Site: {fetch_site}"
    if origin:
        if origin != "null" and urlparse(origin).netloc == request.host:
            return None
        return f"Origin: {origin}"
    referer = request.headers.get("Referer", "")
    if referer:
        host = urlparse(referer).netloc
        return None if host == request.host or host in _trusted_hosts() else f"Referer: {referer}"
    return None


def refuse_cross_site():
    reason = cross_site_reason()
    if reason is None:
        return None
    log.warning("refused cross-site %s %s from %s (%s)",
                request.method, request.path, request.remote_addr, reason)
    message = gettext("This change came from another website, so it was refused. If you were using BioManager, reload the page and try again.")
    if request.headers.get("X-Autosave") == "1" or request.is_json:
        return jsonify({"ok": False, "error": message}), 403
    return message, 403, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------- content security policy

CSP_REPORT_PATH = "/csp-report"


def csp_mode() -> str:
    mode = os.environ.get("BIOMANAGER_CSP", "").strip().lower() or "enforce"
    if mode not in {"enforce", "report-only", "off"}:
        raise RuntimeError("BIOMANAGER_CSP must be enforce, report-only or off")
    return mode


def csp_nonce() -> str:
    """This request's nonce, for <script nonce="{{ csp_nonce() }}">."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(18)
    return g.csp_nonce


def content_security_policy() -> str:
    script = f"'self' 'nonce-{csp_nonce()}'"
    # A view whose vendor bundle compiles code at runtime can widen this for
    # its own response only: g.csp_script_extra = "'unsafe-eval'".
    if g.get("csp_script_extra"):
        script += " " + g.csp_script_extra
    return "; ".join([
        "default-src 'self'",
        f"script-src {script}",
        # Inline style attributes are everywhere in the templates; styles
        # cannot run script, so this is the part of the policy left open.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
        f"report-uri {CSP_REPORT_PATH}",
    ])


def csp_report():
    """Browsers post here what the policy blocked. Logged, nothing stored."""
    # Anyone can post here, so: a few a minute per address, and each value
    # one short line (a newline in a field wrote a fake log line).
    if report_throttle.retry_after(("csp", request.remote_addr or "")):
        return "", 204
    report_throttle.failed(("csp", request.remote_addr or ""))
    raw = request.get_data(cache=False, as_text=True)[:8192]
    one_line = lambda v: repr(str(v or "")[:200])
    try:
        import json
        body = json.loads(raw or "{}")
        report = body.get("csp-report", body)
        log.warning("CSP blocked %s on %s (%s)", one_line(report.get("blocked-uri") or report.get("blockedURL")),
                    one_line(report.get("document-uri") or report.get("documentURL")),
                    one_line(report.get("violated-directive") or report.get("effectiveDirective")))
    except (ValueError, AttributeError):
        log.warning("CSP report that could not be read: %r", raw[:300])
    return "", 204


# ---------------------------------------------------------------- uploads and headers

def guard_uploads():
    if request.path.startswith(UPLOADS_PREFIX) and g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def add_security_headers(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "same-origin")
    if request.is_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    mode = csp_mode()
    if mode != "off" and response.mimetype == "text/html" and not request.path.startswith(UPLOADS_PREFIX):
        name = "Content-Security-Policy" if mode == "enforce" else "Content-Security-Policy-Report-Only"
        headers.setdefault(name, content_security_policy())
    if request.path.startswith(UPLOADS_PREFIX):
        headers["Cache-Control"] = "private, max-age=3600"
        if response.mimetype not in INLINE_UPLOADS:
            headers["Content-Disposition"] = "attachment"
        if response.mimetype != "application/pdf":
            # Should an uploaded page be opened anyway, it runs with no
            # origin: it cannot read the app, its cookies or its data.
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
    return response


def too_large(_error):
    limit = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    message = gettext("That upload is too large. The limit is %(limit)s MB.", limit=limit)
    if request.headers.get("X-Autosave") == "1" or request.is_json:
        return jsonify({"ok": False, "error": message}), 413
    flash(message, "error")
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) else url_for("home_dashboard"))


# ---------------------------------------------------------------- sign-in

def safe_next(target: str | None) -> str | None:
    """`target` if it is a path on this site, else None: a `next=` link
    must not send someone who just signed in to another site."""
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    if any(ord(ch) < 0x20 for ch in target):  # browsers drop tabs/newlines: "/\t/evil"
        return None
    parsed = urlparse(target)
    return target if not parsed.scheme and not parsed.netloc else None


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return gettext("Use a password of at least %(n)s characters.", n=MIN_PASSWORD_LENGTH)
    if username and password.strip().lower() == username.strip().lower():
        return gettext("Your password cannot be your username.")
    return None


_DUMMY_HASH: list[str] = []

# The stored "hash" of an account that signs in only with Google or
# Microsoft. No password matches it; an admin reset, or setting one in
# Settings, gives the account a password as well.
NO_PASSWORD = "!no-password"


def has_password(user) -> bool:
    return bool(user.password_hash) and not user.password_hash.startswith("!")


def check_password(user, password: str) -> bool:
    """check_password_hash, taking as long for an unknown username, or an
    account with no password, as for a wrong password, so the timing does
    not reveal which accounts exist or how they sign in."""
    if user is None or not has_password(user):
        if not _DUMMY_HASH:
            _DUMMY_HASH.append(generate_password_hash(secrets.token_hex(16)))
        check_password_hash(_DUMMY_HASH[0], password)
        return False
    try:
        return check_password_hash(user.password_hash, password)
    except ValueError:  # not a hash werkzeug recognises
        return False


def start_session(user) -> None:
    """Sign `user` in on this browser: a fresh session, bound to the
    password it was started under (session_stamp), with a rolling lifetime."""
    from flask import session

    lang = session.get("lang")
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["auth"] = session_stamp(user)
    session["sid"] = secrets.token_urlsafe(18)
    if lang:
        # The language picked on the sign-in page carries on (app/i18n.py).
        session["lang"] = lang


class LoginThrottle:
    """At most `limit` failed sign-ins per key in any `window` seconds.

    Kept in memory, so each worker process counts on its own and a restart
    forgets: with N workers someone gets up to N x `limit` guesses per
    window, which still turns a brute-force attack from hours into years."""

    def __init__(self, limit: int = 10, window: int = 15 * 60):
        self.limit = limit
        self.window = window
        self._failures: dict[tuple, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key, now: float) -> list[float]:
        stamps = [t for t in self._failures.get(key, ()) if now - t < self.window]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def retry_after(self, *keys) -> int:
        """Seconds until these keys may try again; 0 when they may now."""
        now = time.monotonic()
        with self._lock:
            waits = [int(self.window - (now - stamps[-self.limit])) + 1
                     for stamps in (self._recent(k, now) for k in keys) if len(stamps) >= self.limit]
        return max(waits, default=0)

    def failed(self, *keys) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._failures) > 10_000:  # a scan of many names: drop what has aged out
                for key in list(self._failures):
                    self._recent(key, now)
            for key in keys:
                self._failures.setdefault(key, []).append(now)

    def succeeded(self, *keys) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()
# Sign-ups per address (each notifies every admin), wrong current passwords
# in Settings per account, and CSP reports per address.
signup_throttle = LoginThrottle(limit=5, window=60 * 60)
password_check_throttle = LoginThrottle(limit=10, window=15 * 60)
report_throttle = LoginThrottle(limit=30, window=60)


def login_keys(username: str) -> tuple:
    return (("ip", request.remote_addr or ""), ("user", username.strip().lower()))


def https_required_but_missing() -> bool:
    """True when cookies are Secure but this request came over plain HTTP,
    where the browser will silently drop them and sign-in cannot work."""
    return bool(current_app.config.get("SESSION_COOKIE_SECURE")) and not request.is_secure


# ---------------------------------------------------------------- the first account

def _setup_code_path() -> Path:
    return data_dir() / "setup-code"


def setup_code() -> str:
    return _read_or_create(_setup_code_path(),
                           lambda: "-".join(secrets.token_hex(2) for _ in range(3)))


def setup_code_required() -> bool:
    """The desktop app sets LOCAL_SETUP: it listens on 127.0.0.1 only, and
    the person at the keyboard is the only one who can reach it, unless it
    shares its lab on the network (app/devices.py): from there, the code."""
    from flask import has_request_context, request
    if has_request_context() and request.environ.get("biomanager.lan") == "1":
        return True
    return not current_app.config.get("LOCAL_SETUP")


def setup_code_matches(entered: str | None) -> bool:
    entered = (entered or "").strip().lower()
    return bool(entered) and hmac.compare_digest(entered, setup_code())


def clear_setup_code() -> None:
    _setup_code_path().unlink(missing_ok=True)


def announce_setup_code(logger) -> None:
    """Print the code needed to create the first (admin) account."""
    code = setup_code()
    logger.warning("No accounts yet. Create the first admin at /register with setup code %s "
                   "(also saved in %s).", code, _setup_code_path())
