"""Set up a lab server from the desktop app: a wizard that asks where it goes
and then does the work (templates/server_setup.html, static/server-setup.js).

Where a server can go, as in the website's "Run it for your lab":

- a cloud VM reached privately over Tailscale (recommended), or with the
  lab's own web address and a Let's Encrypt certificate;
- a university or department server;
- a lab computer: a Linux machine over SSH, or this computer itself when it
  has Docker.

Every one of them gets the same thing: the release's server bundle and app
image (deploy/host/load-image.sh, so no source or registry is needed),
deploy/.env with a fresh database password, optionally this desktop app's
records moved in (scripts/migrate-to-postgres.py), `docker compose up -d`,
the watchdog and maintenance timers (deploy/host/install.sh), and a check
that it answers. One bash script does it (build_script), run over SSH or
here; it reports progress as `::step::`, `::value::KEY=…`, `::done::` and
`::fail::` lines, which the page shows as it goes.

Only the desktop app offers this (app.config["LOCAL_SETUP"], set by
desktop.py): it runs commands on this computer and over its SSH keys.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from .i18n import gettext
from .paths import data_dir

bp = Blueprint("server_setup", __name__, url_prefix="/server-setup")

BUNDLE_URL = "https://github.com/gaspolymerase/biomanager/releases/latest/download/biomanager-server.tar.gz"
REMOTE_BASE = "/opt/biomanager"
LOCAL_BASE = "~/BioManagerServer"

# name: (title, how it is reached, how the address is chosen)
TARGETS = {
    "cloud-tailscale": {"title": "A cloud server, private over Tailscale", "remote": True, "tls": "tailscale"},
    "cloud-domain": {"title": "A cloud server with the lab's own web address", "remote": True, "tls": "acme"},
    "university": {"title": "A university or department server", "remote": True, "tls": "internal"},
    "lab-linux": {"title": "A Linux computer in the lab", "remote": True, "tls": "internal"},
    "this-computer": {"title": "This computer, for the whole lab", "remote": False, "tls": "internal"},
}

_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_USER = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TZ = re.compile(r"^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.before_request
def desktop_only():
    # Only the person at the desktop: not a device that reaches a desktop
    # sharing its lab on the network (app/devices.py).
    from . import devices
    if not current_app.config.get("LOCAL_SETUP") or not devices.on_this_computer():
        abort(404)


# ---------------------------------------------------------------- the plan

@dataclass
class Plan:
    target: str
    host: str = ""
    user: str = ""
    port: int = 22
    key_path: str = ""
    password: str = ""         # signing in with a password instead of a key (a NAS); never kept
    sudo_password: str = ""    # for an account whose sudo asks for one (a NAS's admin); never kept
    sudo_from_login: bool = False   # sudo_password is the sign-in password, tried first
    address: str = ""          # what people type; Tailscale fills it in itself
    acme_email: str = ""
    ts_authkey: str = ""
    ts_hostname: str = "biomanager"
    timezone: str = "UTC"
    bring_data: bool = False

    @property
    def remote(self) -> bool:
        return TARGETS[self.target]["remote"]

    @property
    def tls(self) -> str:
        return TARGETS[self.target]["tls"]

    @property
    def base(self) -> str:
        return REMOTE_BASE if self.remote else LOCAL_BASE


def plan_from(data: dict) -> tuple[Plan | None, list[str]]:
    """A Plan from the page's answers, or what is wrong with them."""
    problems = []
    target = str(data.get("target") or "")
    if target not in TARGETS:
        return None, [gettext("Choose where the server goes.")]
    plan = Plan(target=target)
    plan.timezone = str(data.get("timezone") or "UTC").strip()
    import zoneinfo
    if not _TZ.match(plan.timezone) or plan.timezone not in zoneinfo.available_timezones():
        problems.append(gettext("The time zone should look like Europe/London or America/New_York."))
    plan.bring_data = bool(data.get("bring_data"))
    if plan.remote:
        plan.host = str(data.get("host") or "").strip()
        plan.user = str(data.get("user") or "").strip()
        plan.key_path = str(data.get("key_path") or "").strip()
        try:
            plan.port = int(data.get("port") or 22)
        except (TypeError, ValueError):
            plan.port = 0
        if not _HOST.match(plan.host):
            problems.append(gettext("Give the server's address: an IP address or a host name."))
        if not _USER.match(plan.user):
            problems.append(gettext("Give the user name you sign in to the server with (often ubuntu or your university ID)."))
        if not 0 < plan.port < 65536:
            problems.append(gettext("The SSH port is a number, usually 22."))
        if plan.key_path and not Path(os.path.expanduser(plan.key_path)).is_file():
            problems.append(gettext("There is no key file at %(path)s.", path=plan.key_path))
        plan.password = str(data.get("password") or "")
        if plan.password:
            plan.key_path = ""
        if "\n" in plan.password or "\r" in plan.password or len(plan.password) > 256:
            problems.append(gettext("That password can't be used: it has a line break in it, or is very long."))
        plan.sudo_password = str(data.get("sudo_password") or "")
        if "\n" in plan.sudo_password or "\r" in plan.sudo_password or len(plan.sudo_password) > 256:
            problems.append(gettext("That sudo password can't be used: it has a line break in it, or is very long."))
        if not plan.sudo_password and plan.password:
            # Usually the same password (a NAS's admin): try it before asking for another.
            plan.sudo_password, plan.sudo_from_login = plan.password, True
    if target == "cloud-tailscale":
        plan.ts_authkey = str(data.get("ts_authkey") or "").strip()
        plan.ts_hostname = str(data.get("ts_hostname") or "biomanager").strip().lower()
        if not plan.ts_authkey.startswith("tskey-"):
            problems.append(gettext("Paste a Tailscale auth key: it starts with tskey-."))
        if not re.match(r"^[a-z0-9-]{1,63}$", plan.ts_hostname):
            problems.append(gettext("The server's Tailscale name uses letters, digits and hyphens."))
    else:
        plan.address = str(data.get("address") or "").strip().lower()
        if not _HOST.match(plan.address):
            problems.append(gettext("Give the address people will type to reach BioManager."))
    if target == "cloud-domain":
        plan.acme_email = str(data.get("acme_email") or "").strip()
        if not _EMAIL.match(plan.acme_email):
            problems.append(gettext("Give an email address for the certificate (Let's Encrypt writes there before it expires)."))
    return plan, problems


def ssh_base(plan: Plan) -> list[str]:
    command = ["ssh", "-p", str(plan.port), "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
    if plan.password:
        # Asked for by ssh_password()'s helper; one try, so a wrong one fails at once.
        command += ["-o", "PreferredAuthentications=keyboard-interactive,password", "-o", "PubkeyAuthentication=no",
                    "-o", "NumberOfPasswordPrompts=1"]
    else:
        command += ["-o", "BatchMode=yes"]
        if plan.key_path:
            command += ["-i", os.path.expanduser(plan.key_path)]
    return command + [f"{plan.user}@{plan.host}"]


ASKPASS_MODE = "BIOMANAGER_ASKPASS"      # desktop.py answers as the helper when this is set
ASKPASS_SECRET = "BIOMANAGER_SSH_PASSWORD"


@contextmanager
def ssh_password(plan: Plan):
    """What ssh needs to sign in with plan.password, as keyword arguments for
    subprocess: an environment naming a helper that prints the password
    (SSH_ASKPASS), so it is never on a command line. Empty for a key."""
    if not plan.password:
        yield {}
        return
    folder = tempfile.mkdtemp(prefix="biomanager-ssh-")
    try:
        env = {**os.environ, ASKPASS_SECRET: plan.password, "SSH_ASKPASS_REQUIRE": "force",
               # ssh before 8.4 has no SSH_ASKPASS_REQUIRE: it uses the helper when
               # there is a DISPLAY and no terminal (start_new_session below).
               "DISPLAY": os.environ.get("DISPLAY") or ":0"}
        if os.name == "nt" and getattr(sys, "frozen", False):
            # No shell scripts here: the app itself prints it (desktop.py).
            env.update({"SSH_ASKPASS": sys.executable, ASKPASS_MODE: "1"})
        elif os.name == "nt":
            helper = Path(folder) / "askpass.cmd"
            helper.write_text(f'@"{sys.executable}" -c "import os; print(os.environ[\'{ASKPASS_SECRET}\'])"\r\n',
                              encoding="utf-8")
            env["SSH_ASKPASS"] = str(helper)
        else:
            helper = Path(folder) / "askpass"
            helper.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "${ASKPASS_SECRET}"\n', encoding="utf-8")
            helper.chmod(0o700)
            env["SSH_ASKPASS"] = str(helper)
        yield {"env": env, "start_new_session": os.name != "nt"}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- the script

def build_script(plan: Plan, show_secrets: bool = True) -> str:
    """The bash script that sets the server up. With show_secrets off (for
    showing it on the page) the Tailscale key is left out."""
    q = shlex.quote
    authkey = plan.ts_authkey if show_secrets else "tskey-…(hidden)"
    sudo_password = plan.sudo_password if show_secrets or not plan.sudo_password else "(hidden)"
    header = "\n".join([
        f"BASE={plan.base}",
        f"TARGET={q(plan.target)}",
        f"TLS={q(plan.tls)}",
        f"ADDRESS={q(plan.address)}",
        f"ACME_EMAIL={q(plan.acme_email)}",
        f"TZ_NAME={q(plan.timezone)}",
        f"TS_AUTHKEY={q(authkey)}",
        f"SUDO_PASSWORD={q(sudo_password)}",
        f"TS_HOSTNAME={q(plan.ts_hostname)}",
        f"BRING_DATA={'1' if plan.bring_data else '0'}",
        f"BUNDLE_URL={q(BUNDLE_URL)}",
    ])
    return f"""#!/usr/bin/env bash
# Sets up BioManager for a lab. Written by the desktop app's server set-up.
set -euo pipefail
{header}
BASE=$(eval echo "$BASE")
trap 'echo "::fail::Stopped at: $BASH_COMMAND"' ERR
step() {{ echo "::step::$1"; }}
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"
# On this computer everything goes in the user's own folder, through Docker Desktop.
[ "$TARGET" != this-computer ] || SUDO=""
if [ -n "$SUDO" ] && [ -n "$SUDO_PASSWORD" ]; then
  # An account whose sudo asks for its password (a NAS's admin): sudo gets it
  # from this helper, never from a command line, and commands keep their input.
  askpass=$(mktemp "${{HOME:-/tmp}}/.biomanager-askpass.XXXXXX") && chmod 700 "$askpass"
  cat > "$askpass" <<'ASKPASS'
#!/bin/sh
printf '%s\\n' "$BIOMANAGER_SUDO_PASSWORD"
ASKPASS
  trap 'rm -f "$askpass"' EXIT
  export SUDO_ASKPASS="$askpass" BIOMANAGER_SUDO_PASSWORD="$SUDO_PASSWORD"
  SUDO="sudo -A"
fi
have() {{ command -v "$1" >/dev/null 2>&1; }}

step "Checking the machine"
arch=$(uname -m)
case "$arch" in
  x86_64|amd64|aarch64|arm64) ;;
  *) echo "::fail::BioManager runs on x86-64 and ARM64 computers, not $arch."; exit 1 ;;
esac
if [ -r /etc/os-release ]; then . /etc/os-release; echo "${{PRETTY_NAME:-Linux}}, $arch"; else echo "$(uname -s), $arch"; fi
if [ -n "$SUDO" ] && [ "$(uname -s)" = Linux ]; then
  if [ -n "$SUDO_PASSWORD" ]; then
    $SUDO -v 2>/dev/null || {{ echo "::fail::sudo didn't accept the password, or this account may not use sudo. Check it and run the set-up again."; exit 1; }}
  elif ! sudo -n true 2>/dev/null; then
    echo "::fail::This account can't use sudo without a password. Give its password on the Connect step, or sign in as an account that can (on cloud servers, usually ubuntu)."
    exit 1
  fi
fi
if [ -f "$BASE/Biomanager/deploy/.env" ]; then
  echo "::fail::BioManager is already set up on this machine ($BASE). To update it, see the runbook's Updating section."
  exit 1
fi

step "Installing Docker"
if ! have docker; then
  if [ "$(uname -s)" = Linux ] && have apt-get; then
    $SUDO install -m 0755 -d /etc/apt/keyrings
    distro=${{ID:-ubuntu}}; [ "$distro" = debian ] || distro=ubuntu
    $SUDO curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro ${{VERSION_CODENAME:-noble}} stable" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
    $SUDO apt-get update -q
    $SUDO apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    $SUDO systemctl enable --now docker
  else
    echo "::fail::Docker isn't installed here. Install Docker (Docker Desktop on a Mac, Docker Engine on Linux), then run the set-up again."
    exit 1
  fi
else
  echo "Docker is already installed."
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"
$DOCKER compose version >/dev/null 2>&1 || {{ echo "::fail::Docker's compose plugin is missing. Install docker-compose-plugin (Linux) or update Docker Desktop."; exit 1; }}

if [ "$TLS" = tailscale ]; then
  step "Joining your Tailscale network"
  have tailscale || curl -fsSL https://tailscale.com/install.sh | $SUDO sh
  $SUDO tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME"
  ADDRESS=$($SUDO tailscale status --json --peers=false | grep -o '"DNSName": *"[^"]*"' | head -1 | sed 's/.*"\\([^"]*\\)"$/\\1/; s/\\.$//')
  [ -n "$ADDRESS" ] || {{ echo "::fail::Tailscale joined but gave no name. Turn on MagicDNS in the Tailscale admin console and run the set-up again."; exit 1; }}
  echo "Its address on your tailnet: $ADDRESS"
fi
echo "::value::ADDRESS=$ADDRESS"

step "Downloading BioManager"
$SUDO mkdir -p "$BASE" && $SUDO chown "$(id -un)" "$BASE"
curl -fL --retry 3 -o "$BASE/biomanager-server.tar.gz" "$BUNDLE_URL"
tar -xzf "$BASE/biomanager-server.tar.gz" -C "$BASE"
rm -f "$BASE/biomanager-server.tar.gz"
echo "Version $(cat "$BASE/Biomanager/VERSION" 2>/dev/null || echo latest)"
if [ "$DOCKER" = docker ]; then bash "$BASE/Biomanager/deploy/host/load-image.sh"; else $SUDO bash "$BASE/Biomanager/deploy/host/load-image.sh"; fi

step "Writing its settings"
cd "$BASE/Biomanager/deploy"
cp .env.example .env && chmod 600 .env
password=$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \\n')
setenv() {{ sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; }}
setenv DOMAIN "$ADDRESS"
setenv TLS "$TLS"
setenv POSTGRES_PASSWORD "$password"
setenv TZ "$TZ_NAME"
[ -z "$ACME_EMAIL" ] || setenv ACME_EMAIL "$ACME_EMAIL"
if [ "$TLS" = tailscale ]; then sed -i.bak 's|^#COMPOSE_FILE=|COMPOSE_FILE=|' .env && rm -f .env.bak; fi
echo "Written to $BASE/Biomanager/deploy/.env (readable by this account only)."
# 80 and 443, or others when this machine already uses them (a NAS's own pages).
ports=$(bash host/ports.sh) || {{ echo "::fail::$(echo "$ports" | tr '\\n' ' ')"; exit 1; }}
echo "$ports"
https_port=$(grep -E '^HTTPS_PORT=' .env | tail -1 | cut -d= -f2)
[ "${{https_port:-443}}" = 443 ] || echo "::value::ADDRESS=$ADDRESS:$https_port"

if [ "$BRING_DATA" = 1 ]; then
  step "Moving your records in"
  [ -f "$BASE/import/lab.db" ] || {{ echo "::fail::The desktop app's database didn't arrive."; exit 1; }}
  $DOCKER compose up -d db
  sleep 5
  $DOCKER compose run --rm --no-deps -v "$BASE/import/lab.db:/import/lab.db:ro" app \\
    sh -c 'python scripts/migrate-to-postgres.py /import/lab.db "$DATABASE_URL"'
fi

step "Starting BioManager"
$DOCKER compose up -d
for i in $(seq 1 60); do
  state=$($DOCKER compose ps app --format '{{{{.Health}}}}' 2>/dev/null || true)
  [ "$state" = healthy ] && break
  sleep 3
done
[ "$state" = healthy ] || {{ echo "::fail::BioManager didn't come up. Its log: docker compose logs app (in $BASE/Biomanager/deploy)."; $DOCKER compose logs --tail 40 app || true; exit 1; }}
echo "Running."
if [ "$BRING_DATA" = 1 ] && [ -f "$BASE/import/uploads.tar" ]; then
  mkdir -p "$BASE/import/uploads" && tar -xf "$BASE/import/uploads.tar" -C "$BASE/import/uploads"
  $DOCKER compose cp "$BASE/import/uploads/." app:/data/uploads/
fi
rm -rf "$BASE/import"
code=$($DOCKER compose logs app 2>&1 | grep -o 'setup code [0-9a-f-]*' | tail -1 | awk '{{print $3}}' || true)
[ -z "$code" ] || echo "::value::SETUP_CODE=$code"

if have systemctl && [ -d /run/systemd/system ]; then
  step "Alerts, backups and updates"
  out=$($SUDO bash "$BASE/Biomanager/deploy/host/install.sh" 2>&1 || true)
  echo "$out" | head -3
  topic=$(echo "$out" | grep -o 'ntfy topic: [^ ]*' | awk '{{print $3}}' || true)
  [ -z "$topic" ] || echo "::value::NTFY_TOPIC=$topic"
fi

echo "::done::"
"""


# ---------------------------------------------------------------- running it

@dataclass
class Job:
    id: str
    plan: Plan
    lines: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    status: str = "running"    # running | done | failed
    error: str = ""
    started: float = field(default_factory=time.time)


JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _say(job: Job, line: str) -> None:
    secret = job.plan.ts_authkey
    if secret:
        line = line.replace(secret, "tskey-…")
    for password in (job.plan.password, job.plan.sudo_password):
        if password:
            line = line.replace(password, "••••••")
    line = line.rstrip()
    if line.startswith("::step::"):
        job.steps.append(line[8:])
    elif line.startswith("::value::") and "=" in line:
        key, value = line[9:].split("=", 1)
        job.values[key] = value
    elif line.startswith("::fail::"):
        job.error = line[8:]
    elif line == "::done::":
        pass
    elif line and len(job.lines) < 5000:
        job.lines.append(line[:500])


def _export_data(tmp: Path) -> list[Path]:
    """A consistent copy of this app's database (SQLite's backup API) and a
    tarball of its uploaded files."""
    from .db import engine
    from .services import UPLOAD_DIR

    files = []
    source = engine.url.database
    if engine.dialect.name != "sqlite" or not source:
        raise RuntimeError("Only the desktop app's own SQLite database can be moved.")
    target = tmp / "lab.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    files.append(target)
    uploads = Path(UPLOAD_DIR)
    if uploads.is_dir() and any(uploads.iterdir()):
        archive = tmp / "uploads.tar"
        with tarfile.open(archive, "w") as tar:
            for item in uploads.iterdir():
                tar.add(item, arcname=item.name)
        files.append(archive)
    return files


def _stream(job: Job, command: list[str], stdin_text: str | None = None, stdin_file: Path | None = None,
            ssh: dict | None = None) -> int:
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=stdin_file is None, bufsize=1 if stdin_file is None else -1, **(ssh or {}))
    if stdin_file is not None:
        with open(stdin_file, "rb") as handle:
            shutil.copyfileobj(handle, proc.stdin)
        proc.stdin.close()
        out = proc.stdout.read().decode(errors="replace")
        for line in out.splitlines():
            _say(job, line)
        return proc.wait()
    proc.stdin.write(stdin_text or "")
    proc.stdin.close()
    for line in proc.stdout:
        _say(job, line)
    return proc.wait()


def _run(job: Job) -> None:
    plan = job.plan
    try:
        with tempfile.TemporaryDirectory() as tmp_name, ssh_password(plan) as ssh:
            tmp = Path(tmp_name)
            if plan.bring_data:
                job.steps.append("Copying this app's records")
                files = _export_data(tmp)
                if plan.remote:
                    # A folder this account owns, made in one sudo of its own: the
                    # copies below send the files as their input, with no room for a
                    # password (sudo -S reads it, when there is one, from this input).
                    prep = (f"cmd=\"mkdir -p {REMOTE_BASE}/import && chown $(id -un) {REMOTE_BASE} {REMOTE_BASE}/import\"; "
                            "if [ \"$(id -u)\" = 0 ]; then sh -c \"$cmd\"; else sudo -S -p '' sh -c \"$cmd\"; fi")
                    if _stream(job, ssh_base(plan) + [prep], ssh=ssh,
                               stdin_text=plan.sudo_password + "\n" if plan.sudo_password else "") != 0:
                        raise RuntimeError(f"Couldn't make {REMOTE_BASE}/import on the server.")
                for f in files:
                    if plan.remote:
                        remote = f"{REMOTE_BASE}/import/{f.name}"
                        if _stream(job, ssh_base(plan) + [f"cat > {remote}"], stdin_file=f, ssh=ssh) != 0:
                            raise RuntimeError(f"Couldn't copy {f.name} to the server.")
                    else:
                        local = Path(os.path.expanduser(LOCAL_BASE)) / "import"
                        local.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, local / f.name)
                    job.lines.append(f"Copied {f.name} ({f.stat().st_size // 1024} KB).")
            script = build_script(plan)
            command = ssh_base(plan) + ["bash -s"] if plan.remote else ["bash", "-s"]
            code = _stream(job, command, stdin_text=script, ssh=ssh)
            if code != 0 or job.error:
                raise RuntimeError(job.error or f"The set-up stopped (exit {code}).")
        address = job.values.get("ADDRESS") or plan.address
        job.steps.append("Checking it answers from here")
        job.values["REACHABLE"] = _check_from_here(address)
        _remember(job)
        job.status = "done"
    except Exception as exc:  # shown on the page; nothing is left half-remembered
        job.error = job.error or str(exc)
        job.status = "failed"
    finally:
        job.plan.password = job.plan.sudo_password = ""  # used for this run only


def _check_from_here(address: str) -> str:
    """"yes", "yes-own-certificate" (TLS=internal), or why not."""
    url = f"https://{address}/healthz"
    for attempt in range(10):
        try:
            urlopen(url, timeout=8).read()
            return "yes"
        except ssl.SSLError:
            try:
                urlopen(url, timeout=8, context=ssl._create_unverified_context()).read()  # noqa: S323 (only a reachability check)
                return "yes-own-certificate"
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(3)
    return "no"


def _remember(job: Job) -> None:
    """What was set up where, for the page to show next time (no secrets)."""
    record = {"target": job.plan.target, "address": job.values.get("ADDRESS") or job.plan.address,
              "host": job.plan.host, "user": job.plan.user, "set_up": datetime.now().isoformat(timespec="minutes")}
    path = data_dir() / "lab-server.json"
    try:
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        pass


def remembered() -> dict | None:
    try:
        return json.loads((data_dir() / "lab-server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_connection(plan: Plan) -> dict:
    """Can we reach it, and what is it? Runs a few read-only commands."""
    # sudo: root, yes (no password), password (it asks for one; none given yet),
    # password-ok (the one given works) or no (the one given doesn't, or sudo is refused).
    if plan.sudo_password:
        sudo = ("(sudo -n true 2>/dev/null && echo sudo=yes) || "
                "(sudo -S -p '' -v 2>/dev/null && echo sudo=password-ok) || echo sudo=no")
    else:
        sudo = ("(sudo -n true 2>/dev/null && echo sudo=yes) || "
                "(sudo -n true 2>&1 | grep -q 'password is required' && echo sudo=password) || echo sudo=no")
    probe = ("uname -sm; (. /etc/os-release 2>/dev/null && echo \"os=$PRETTY_NAME\") || true; "
             "command -v docker >/dev/null && echo docker=yes || echo docker=no; "
             f"if [ \"$(id -u)\" = 0 ]; then echo sudo=root; else {sudo}; fi; "
             f"[ -f {REMOTE_BASE}/Biomanager/deploy/.env ] && echo existing=yes || echo existing=no")
    try:
        # The sudo password, if any, goes in on standard input, for sudo -S only.
        with ssh_password(plan) as ssh:
            out = subprocess.run(ssh_base(plan) + [probe], capture_output=True, text=True, timeout=40,
                                 input=plan.sudo_password + "\n" if plan.sudo_password else "", **ssh)
    except FileNotFoundError:
        return {"ok": False, "error": gettext("This computer has no ssh command. On Windows, add the OpenSSH Client under Settings → Apps → Optional features.")}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": gettext("The server didn't answer within 40 seconds. Check the address, and that it allows SSH from this computer.")}
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["no answer"]
        hint = ""
        if plan.password and "askpass" in (out.stderr or ""):
            hint = " " + gettext("This computer's ssh can't take a password from BioManager. Sign in with an SSH key instead.")
        elif "Permission denied" in detail[0] and plan.password:
            hint = " " + gettext("The server didn't accept this password: check the user name and the password, and that the server allows signing in with a password.")
        elif "Permission denied" in detail[0]:
            hint = " " + gettext("The server didn't accept this key: check the user name and the key file.")
        elif "Could not resolve" in detail[0]:
            hint = " " + gettext("That address doesn't resolve: check it for typos.")
        return {"ok": False, "error": gettext("Couldn't sign in to %(host)s: %(detail)s.", host=plan.host,
                                              detail=detail[0].rstrip(".")) + hint}
    info = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    first = out.stdout.splitlines()[0] if out.stdout else ""
    return {"ok": True, "system": info.get("os") or first, "machine": first.split()[-1] if first else "",
            "docker": info.get("docker") == "yes", "sudo": info.get("sudo") in ("yes", "root", "password-ok"),
            # The sign-in password, tried for sudo, not working: ask for sudo's own.
            "sudo_password": "needed" if plan.sudo_from_login and info.get("sudo") == "no" else
                             "wrong" if plan.sudo_password and info.get("sudo") == "no" else
                             "needed" if info.get("sudo") in ("password", "password-ok") else "",
            "existing": info.get("existing") == "yes"}


def local_readiness() -> dict:
    """For "this computer": is Docker (with compose) here and running?"""
    if os.name == "nt":
        return {"ok": False, "error": gettext("On Windows, run the lab server on a Linux computer or cloud server, or in WSL.")}
    if not shutil.which("docker"):
        return {"ok": False, "error": gettext("Docker isn't installed. Install Docker Desktop (docker.com), start it, and try again.")}
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if probe.returncode != 0:
        return {"ok": False, "error": gettext("Docker's compose command is missing. Update Docker Desktop and try again.")}
    running = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if running.returncode != 0:
        return {"ok": False, "error": gettext("Docker is installed but not running. Start Docker Desktop and try again.")}
    return {"ok": True, "system": gettext("This computer"), "docker": True, "sudo": True, "existing":
            Path(os.path.expanduser(LOCAL_BASE), "Biomanager", "deploy", ".env").exists()}


def this_timezone() -> str:
    """This computer's time zone name (Europe/London), for the lab's default:
    TZ if set, else what /etc/localtime points at (macOS and Linux)."""
    import zoneinfo
    name = os.environ.get("TZ", "").strip()
    if not name:
        try:
            target = os.path.realpath("/etc/localtime")
            name = target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""
        except OSError:
            name = ""
    return name if name in zoneinfo.available_timezones() else "UTC"


# ---------------------------------------------------------------- routes

@bp.route("/")
def page():
    import zoneinfo
    zones = sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    zones.append("UTC")
    local_tz = this_timezone()
    keys = [str(p) for p in sorted(Path.home().glob(".ssh/id_*")) if not p.name.endswith(".pub")]
    from sqlalchemy import func, select

    from .db import SessionLocal
    from .models import UserAccount
    with SessionLocal() as session:
        accounts = session.scalar(select(func.count(UserAccount.id))) or 0
    import socket
    name = socket.gethostname().split(".")[0].lower()
    return render_template("server_setup.html", targets=TARGETS, zones=zones, local_tz=local_tz or "UTC",
                           keys=keys, remembered=remembered(), accounts=accounts,
                           computer_address=f"{name}.local" if name else "")


@bp.route("/check", methods=["POST"])
def check():
    plan, problems = plan_from(request.get_json(silent=True) or {})
    if plan is None:
        return jsonify({"ok": False, "error": problems[0]}), 400
    if plan.remote:
        if not _HOST.match(plan.host) or not _USER.match(plan.user):
            return jsonify({"ok": False, "error": gettext("Give the server's address and your user name on it first.")}), 400
        return jsonify(check_connection(plan))
    return jsonify(local_readiness())


@bp.route("/preview", methods=["POST"])
def preview():
    plan, problems = plan_from(request.get_json(silent=True) or {})
    if problems:
        return jsonify({"ok": False, "errors": problems}), 400
    return jsonify({"ok": True, "script": build_script(plan, show_secrets=False),
                    "runs_on": f"{plan.user}@{plan.host}" if plan.remote else gettext("this computer")})


@bp.route("/start", methods=["POST"])
def start():
    plan, problems = plan_from(request.get_json(silent=True) or {})
    if problems:
        return jsonify({"ok": False, "errors": problems}), 400
    with _JOBS_LOCK:
        if any(j.status == "running" for j in JOBS.values()):
            return jsonify({"ok": False, "errors": [gettext("A set-up is already running.")]}), 409
        job = Job(id=uuid.uuid4().hex, plan=plan)
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return jsonify({"ok": True, "job": job.id})


@bp.route("/job/<job_id>")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    since = int(request.args.get("since", 0) or 0)
    return jsonify({"status": job.status, "steps": job.steps, "lines": job.lines[since:], "next": len(job.lines),
                    "error": job.error, "values": job.values,
                    "tls": job.plan.tls, "target": job.plan.target})


def new_secret() -> str:  # kept for tests that need a plausible key
    return "tskey-auth-" + secrets.token_hex(8)
