# Deploying BioManager: a guide for AI assistants

You are helping someone set up **BioManager**, a lab database for animal
colonies, stocks and inventories, as a server for their lab. This file is
written for you: follow it in order, run the checks, and stop where it says
to ask. The human-readable version is
https://biomanager.org/server.

## Rules

1. **Secrets never pass through the chat.** Passwords, API keys, the
   PostgreSQL password, OAuth client secrets, Backblaze keys, SSH private
   keys: have the person type or paste them directly on the server (in
   `nano .env`, or at a prompt), or generate them there with
   `openssl rand -hex 24`. Do not echo `.env` back into the conversation.
   If you must confirm a value is set, check it is non-empty:
   `grep -c '^POSTGRES_PASSWORD=.\+' .env`.
2. **Ask before anything that costs money, can't be undone, or opens the
   server to the internet**: creating cloud resources, upgrading a cloud
   account, buying a domain, opening firewall ports, `internet-access.sh on`,
   restoring a backup, `docker compose down -v` (that deletes the database).
3. **Accounts are theirs.** The person creates cloud, Tailscale, domain and
   OAuth accounts and signs in to them in their own browser. You tell them
   what to click.
4. **Back up before changing a running server:**
   `docker compose exec backup backup.sh`.
5. **Never expose PostgreSQL.** Only Caddy publishes ports (80, 443, and
   8081 on 127.0.0.1 only). Do not add ports to `db` or `app`.
6. After each step, run its **check**. If a check fails, fix that before
   going on; the troubleshooting table below covers the usual causes.

## First, ask

Ask these, then recommend an option (the table after them) and wait for a yes.

1. Who will use it: one person, or a lab (how many people)?
2. Where are they: all in one building on the same network, or also at home,
   on phones on mobile data, or at other sites?
3. Is there a computer that can stay on (and does IT allow running a server
   on the network), or would they rather use a free cloud VM?
4. Do they want an ordinary web address that needs no app (a domain,
   about $10–15 a year), or is installing Tailscale on each device fine?
5. Do they have the desktop app's data already, to move onto the server?
6. Their time zone (for backups and dates).

| Situation | Option |
| --- | --- |
| One person | **A**: the desktop app. No server. Send them to https://biomanager.org/download and stop here. |
| One building, a Linux computer that stays on | **B**: a Linux computer in the lab, `TLS=internal` (or `TLS=files` with IT's certificate). For a Mac, send them to the desktop app: Settings → Set up a lab server → This computer, for the whole lab |
| University offers VMs | **C**: ask IT for a VM with Docker, a DNS name and a certificate; then as B with `TLS=files` |
| People anywhere, most labs | **D**: Ubuntu 24.04 cloud VM + Tailscale, `TLS=tailscale` (recommended; free on Oracle Cloud Always Free) |
| An address that works with no app | **E**: cloud VM + a domain, `TLS=acme`, ports 80/443 open |

## Facts

- **What runs:** four Docker containers from the server bundle: `app`
  (gunicorn, image `ghcr.io/gaspolymerase/biomanager:<version>`), `db`
  (PostgreSQL 16, internal network only), `caddy` (HTTPS), `backup`
  (nightly `pg_dump` + uploads tarball, weekly restore test, optional
  restic off-site).
- **The bundle:**
  `https://github.com/gaspolymerase/biomanager/releases/latest/download/biomanager-server.tar.gz`.
  It unpacks to `Biomanager/deploy/`; always unpack it into
  `/opt/biomanager`, so the deploy folder is
  `/opt/biomanager/Biomanager/deploy` (scripts and systemd units assume it).
  `BUNDLE.md`, `README.md` and `RUNBOOK.md` inside are the reference.
- **The app image** is not pulled from a registry: the same release has
  `biomanager-image-amd64.tar.gz` and `biomanager-image-arm64.tar.gz`, and
  `deploy/host/load-image.sh` downloads the one for this machine (for the
  version in `deploy/VERSION`) and `docker load`s it with the tag
  `compose.yaml` expects. Run it after every unpack, before `docker compose up`.
  Check: `docker image ls ghcr.io/gaspolymerase/biomanager` shows the version.
- **Settings:** `deploy/.env` (from `.env.example`, mode 600). Required:
  `DOMAIN`, `TLS` (`internal` | `tailscale` | `acme` | `files`),
  `POSTGRES_PASSWORD` (letters and digits only). Usual: `TZ`,
  `BACKUP_DIR=/opt/biomanager/backups`, `BACKUP_TIME=02:30`, `KEEP_LOCAL=30`.
  With `TLS=tailscale` also `COMPOSE_FILE=compose.yaml:compose.tailscale.yaml`.
  With `TLS=acme` also `ACME_EMAIL`. Optional: `RESTIC_*`/`AWS_*` (off-site,
  set by `host/offsite-setup.sh`), `HEALTHCHECK_PING_URL`,
  `BIOMANAGER_GOOGLE_*`/`BIOMANAGER_MICROSOFT_*` (sign-in), `BIOMANAGER_OIDC_*` (the
  institution's own sign-in, OpenID Connect) or `BIOMANAGER_CILOGON_*` (a SAML-only
  university, through CILogon), SMTP, `BIOMANAGER_TELEMETRY=0` (never send
  BioManager's makers the anonymous daily counts; ask the admin, who can
  also switch them off on the Usage report).
- **First account:** the app prints a one-time setup code in its log; the
  first account created at `https://DOMAIN/register` with it is the admin.
  Later sign-ups wait for an admin's approval.
- **Resources:** 2 vCPU, 2 GB RAM, 20 GB disk is plenty. ARM (Oracle Ampere)
  and x86-64 both work.
- **Host extras (Linux with systemd):** `sudo host/install.sh` (from the
  deploy folder) installs a watchdog (every 5 min: site, containers, disk, backups) and
  weekly image updates, alerting to an ntfy topic it prints.
  `host/offsite-setup.sh` sets up encrypted off-site backups (asks for the
  key itself). `host/internet-access.sh on|off|status` opens temporary guest
  access via Tailscale Funnel (option D only).

## Option D: cloud VM + Tailscale (recommended)

**D1. Accounts (the person does these).** Oracle Cloud (free tier; home
region can't be changed later) and Tailscale. Ask them to download the
bundle to their own computer too, and open
`Biomanager/deploy/cloud-init.yaml`.

**D2. The VM (the person clicks; you guide).** Compute → Instances →
Create: Ubuntu 24.04; shape VM.Standard.A1.Flex (2 OCPU/12 GB or 4/24); a
public subnet with a public IPv4; their SSH public key (help them run
`ssh-keygen -t ed25519` if they have none; only the `.pub` goes to Oracle);
Advanced → Management → Initialization script: the contents of
`cloud-init.yaml`. "Out of capacity" means try again later or another
availability domain. Any other provider's Ubuntu 24.04 VM works the same;
then run the steps of `cloud-init.yaml`'s `runcmd` by hand if it has no
cloud-init box.

**D3. Tailscale.**

```bash
ssh ubuntu@<public IP>
test -f /var/lib/biomanager-ready && echo ready    # wait for cloud-init
sudo tailscale up --hostname=biomanager            # the person opens the printed link
```

The person, in the Tailscale admin console: Machines → biomanager →
Disable key expiry; DNS → enable MagicDNS and HTTPS Certificates. Ask them
for the tailnet name (`tailXXXX.ts.net`); it is not secret.
Check: `tailscale status` lists the machine; from their computer, with
Tailscale on, `ssh ubuntu@biomanager` works. Continue over that connection
(a fresh login also puts `ubuntu` in the `docker` group cloud-init added it to).

**D4. BioManager.**

```bash
curl -fsSL -o /tmp/biomanager-server.tar.gz \
  https://github.com/gaspolymerase/biomanager/releases/latest/download/biomanager-server.tar.gz
tar -xzf /tmp/biomanager-server.tar.gz -C /opt/biomanager
cd /opt/biomanager/Biomanager/deploy
host/load-image.sh
cp .env.example .env && chmod 600 .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
sed -i "s|^TLS=.*|TLS=tailscale|; s|^#COMPOSE_FILE=|COMPOSE_FILE=|; s|^BACKUP_DIR=.*|BACKUP_DIR=/opt/biomanager/backups|" .env
sed -i "s|^DOMAIN=.*|DOMAIN=biomanager.tailXXXX.ts.net|; s|^TZ=.*|TZ=<their time zone>|" .env
grep -E '^(DOMAIN|TLS|COMPOSE_FILE|TZ|BACKUP_DIR)=' .env
host/ports.sh
docker compose config --quiet && echo "settings ok"
docker compose up -d --build
```

`host/ports.sh` writes `HTTP_PORT`, `HTTPS_PORT` and `FUNNEL_PORT` into
`.env`: 80, 443 and 8081 when free, others when the machine already uses
them. If it says another port (`BioManager will be at https://…:4443`),
that address, port included, is the one to give everyone below.

Check: `docker compose ps` shows `db` and `app` healthy within a minute or
two, and `backup` healthy once its first backup is done (`health: starting`
until then); `curl -fsS https://biomanager.tailXXXX.ts.net/healthz` prints `ok`;
`docker compose exec backup ls /backups/db` lists a first dump.
Then `docker compose logs app | grep "setup code"`: the person opens
`https://DOMAIN/register` on a device with Tailscale and creates the admin
account with it. The code is single-use; it is fine to show it to them.

**D5. Host extras.** `sudo host/install.sh` (in the deploy folder); tell them to subscribe
to the printed ntfy topic in the ntfy app. Check:
`systemctl list-timers 'biomanager-*'`.

**D6. Close the door (ask first).** In Oracle Cloud, delete the security
list's ingress rule for port 22. Check from their computer:
`ssh -o ConnectTimeout=10 ubuntu@<public IP>` times out, while
`ssh ubuntu@biomanager` keeps working over Tailscale.

**D6b. Oracle's idle stop (optional; ask first).** Oracle stops an Always
Free VM that looks idle for a week (starting it again loses nothing).
Upgrading the account to Pay As You Go (Billing) stops that and still bills
nothing for Always Free resources.

**D7. Off-site backups (recommended; ask first).** The person creates a
Backblaze B2 bucket (private, encryption on, Object Lock on with a 30-day
Governance default) and an application key limited to that bucket. Then,
in their own terminal (the script asks for the key and shows the backup
password once, to save in their password manager):
`ssh -t biomanager sudo /opt/biomanager/Biomanager/deploy/host/offsite-setup.sh`.

**D8. The lab.** Members install Tailscale; the person either invites them
to the tailnet or shares the one machine with their own Tailscale accounts
(Machines → biomanager → Share). Members sign up at the address; the admin
approves them in Settings → People & access. Phones: Android app (download page), or on
iPhone Safari → Share → Add to Home Screen.

## Options B and C: a Linux computer or a university VM

As D4 with `TLS=internal` (B; or `TLS=files` for C, with IT's certificate
and key copied to `deploy/certs/server.crt` and `deploy/certs/server.key`),
`DOMAIN` = a fixed IP or a DNS name that never changes, and no
`COMPOSE_FILE`. Docker: `curl -fsSL https://get.docker.com | sudo sh`
(then `sudo usermod -aG docker $USER` and log in again); turn off sleep.
By hand this is Linux only (the scripts, timers and `/opt` paths assume
it); on a Mac, the desktop app's Settings → Set up a lab server → This
computer, for the whole lab does it. Create `/opt/biomanager` owned by the
user first:
`sudo mkdir -p /opt/biomanager && sudo chown "$USER" /opt/biomanager`.
With `TLS=internal`, every device must trust Caddy's root once:
`docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > biomanager-root.crt`,
then install it (macOS Keychain "Always Trust"; Windows "Trusted Root
Certification Authorities"; iOS profile + Certificate Trust Settings;
Android "CA certificate"). Docker publishes 80 and 443 on the machine
itself; a network firewall run by IT must let 443 through from the lab
network (and the VPN). Put `BACKUP_DIR` on a second disk if there is one.
A NAS with Docker (fnOS and the like) works as B: Docker is already there,
its own web pages usually hold 80 and 443, and `host/ports.sh` moves
BioManager to a free port (4443) by itself; open that port, not 443,
in any firewall. Its storage volume (e.g. `/vol1/…`) is a good `BACKUP_DIR`.

## Option E: a domain

As D, but: a DNS A record `biomanager.<domain>` → the VM's public IP;
inbound TCP 80 and 443 allowed in the cloud firewall (ask first); `.env`
with `DOMAIN=biomanager.<domain>`, `TLS=acme`, `ACME_EMAIL=<their email>`,
no `COMPOSE_FILE`. On Cloudflare DNS, the record must be "DNS only", not
proxied. Check: `docker compose logs caddy | grep "certificate obtained"`
shows `certificate obtained successfully`; `curl -fsS https://biomanager.<domain>/healthz`
from anywhere prints `ok`. Recommend Google or Microsoft sign-in
(`deploy/README.md`, "Signing in with Google or Microsoft") and strong
passwords, since the sign-in page is public.

Cloudflare Tunnel (not packaged; only if they ask): `cloudflared` on the
host pointing at `https://localhost:443` with `originServerName: <DOMAIN>`
(or `noTLSVerify: true` with `TLS=internal`), and Cloudflare Access in
front. Then no inbound ports are needed.

## Moving the desktop app's data in

Only into a **new, empty** server, before its first `docker compose up`
(after `host/load-image.sh` and with `.env` filled in). The app's folder:
macOS `~/Library/Application Support/Biomanager/`, Windows
`%APPDATA%\Biomanager\`, Linux `~/.local/share/Biomanager/`. The database
is `data/biomanager.db` inside it, and the uploaded files are `uploads/`.
The person quits the app and copies both to the server, e.g. from a Mac:

```bash
scp "$HOME/Library/Application Support/Biomanager/data/biomanager.db" <user>@<server>:/tmp/lab.db
scp -r "$HOME/Library/Application Support/Biomanager/uploads" <user>@<server>:/tmp/lab-uploads
```

Then, on the server in the deploy folder:

```bash
docker compose up -d db
docker compose run --rm --no-deps -v /tmp/lab.db:/import/lab.db:ro app \
  sh -c 'python scripts/migrate-to-postgres.py --dry-run /import/lab.db "$DATABASE_URL"'
# if the dry run is clean, the same without --dry-run, then:
docker compose up -d --build
docker compose cp /tmp/lab-uploads/. app:/data/uploads/
```

Check: the person signs in with their existing account (no setup code: the
accounts came along) and sees their data. Then D5 (host extras).

## Updating a running server

```bash
cd /opt/biomanager/Biomanager/deploy
docker compose exec backup backup.sh
curl -fsSL -o /tmp/b.tar.gz https://github.com/gaspolymerase/biomanager/releases/latest/download/biomanager-server.tar.gz
tar -xzf /tmp/b.tar.gz -C /opt/biomanager      # .env and backups are not in the bundle
host/load-image.sh && docker compose up -d --build
```

Check: `docker compose ps` healthy; `curl -fsS https://DOMAIN/healthz`
prints `ok`. The database schema updates itself on start; the log then says
`Upgrading the database from … to …`. If it goes wrong: RUNBOOK.md,
"Updating went wrong".

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `permission denied` on the Docker socket | The user isn't in the `docker` group yet: log out and in, or `newgrp docker` |
| `set POSTGRES_PASSWORD in .env` / `set DOMAIN in .env` | `.env` is missing that value: run the `sed` lines of D4 again, then `docker compose config --quiet` |
| `pull access denied for ghcr.io/gaspolymerase/biomanager` | The image wasn't loaded: run `host/load-image.sh` in the deploy folder, then `docker compose up -d` |
| `app` never healthy | `docker compose logs app`. A malformed `DATABASE_URL` usually means `POSTGRES_PASSWORD` has symbols: use hex only, then `docker compose down` (not `-v`) and `up -d` |
| `/healthz` fails but containers are healthy | `docker compose logs caddy`. `TLS=tailscale`: HTTPS Certificates not enabled in the tailnet, or `COMPOSE_FILE` missing. `TLS=acme`: DNS not pointing at the VM yet, or port 80 closed |
| Browser says the certificate isn't trusted (`TLS=internal`) | Install `biomanager-root.crt` on that device |
| No setup code in the log | An account already exists; the code is only for the first one. `docker compose logs app \| grep -i "setup"` |
| Oracle: "Out of host capacity" | Try later, another availability domain, or a smaller shape |
| A VM on Oracle's free tier stopped after days idle | Oracle stops Always Free VMs that look idle for a week: start it again in Compute → Instances (nothing is lost). Upgrading the account to Pay As You Go prevents it and still bills nothing for Always Free resources |
| Phones can't reach it | Tailscale off on the phone (D), not on the lab network (B), or the root certificate not installed (`TLS=internal`) |

## When you are done

Tell the person, in plain words: the address; that the first account is the
admin; where backups are (`/opt/biomanager/backups`, nightly, restore-tested
weekly) and whether off-site is on; the ntfy topic for alerts; how to update
(above); and that `deploy/RUNBOOK.md` in the bundle says what to do when an
alert arrives. Fill in its table at the top (*Where everything is*) with them:
the address, the server and where to start or rebuild it, where backups go,
off-site or not, the ntfy topic; and have them keep a copy somewhere other
than the server. The user guide is https://biomanager.org/guide.
