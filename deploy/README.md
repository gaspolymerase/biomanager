# Running BioManager on a lab server

Setting it up is below. Once it runs, **[RUNBOOK.md](RUNBOOK.md)** says what
to do when an alert arrives, the site is down, a restore is needed, someone
joins or leaves, or the server is lost.

One machine (a department VM or a lab-owned Linux box) runs the whole
stack in Docker:

```
browser ──HTTPS──▶ caddy ──▶ app (gunicorn) ──▶ db (PostgreSQL 16)
                                  │                  ▲
                         appdata volume              │ pg_dump
                    (uploads, signing key) ◀── backup ┘ ──▶ ./backups ──▶ restic (off-site)
```

Only Caddy listens on the network: on 80 and 443, or the ports
`host/ports.sh` chose when this machine already uses those. The database
sits on an internal Docker network with no route out.

Keep the server **off the open internet**: on the campus network or VPN, or
a private network such as Tailscale.

## First start

On the server, with Docker and the compose plugin installed. Get these files
one of two ways, into `/opt/biomanager/Biomanager/deploy` (the scripts, timers
and RUNBOOK assume that place):

- **The server bundle** from the latest release, no source needed. The app
  image comes as a file from the same release, loaded by `host/load-image.sh`
  (see `BUNDLE.md`):

  ```bash
  sudo mkdir -p /opt/biomanager && sudo chown "$USER" /opt/biomanager
  curl -fsSL -o /tmp/biomanager-server.tar.gz \
    https://github.com/gaspolymerase/biomanager/releases/latest/download/biomanager-server.tar.gz
  tar -xzf /tmp/biomanager-server.tar.gz -C /opt/biomanager
  /opt/biomanager/Biomanager/deploy/host/load-image.sh
  ```

- **Or a checkout** of the source repository, which builds the image itself:
  `git clone <repository> /opt/biomanager/Biomanager`.

Then:

```bash
cd /opt/biomanager/Biomanager/deploy
cp .env.example .env && chmod 600 .env
# edit .env: DOMAIN, POSTGRES_PASSWORD (openssl rand -hex 24), TZ, backups
host/ports.sh
docker compose up -d --build
docker compose logs app | grep "setup code"
```

`host/ports.sh` writes the ports into `.env`: 80 and 443 when they are free.
On a machine that already uses them, such as a NAS whose own web pages hold
80 and 443, it picks others (443, then 4443, 7443, 9443, 10443) and says
so; BioManager's address then carries the port, e.g. `https://nas.local:4443`,
and that is the address people type. The choice is kept from then on.
Let's Encrypt (`TLS=acme`) needs 80 and 443 themselves.

Open `https://DOMAIN/register` (with the port, if `host/ports.sh` chose
one) and create the first account with that setup
code. It becomes the admin. Everyone else who signs up waits for an admin's
approval in Settings → People & access.

### HTTPS

`TLS` in `.env` picks where the certificate comes from:

| `TLS=` | Use when |
| --- | --- |
| `tailscale` | The lab reaches the server over Tailscale (below). A real certificate for its `*.ts.net` name, renewed by Tailscale; nothing to install on lab machines. Also set `COMPOSE_FILE=compose.yaml:compose.tailscale.yaml`, and turn on HTTPS in the Tailscale admin console (DNS → HTTPS Certificates). |
| `internal` (default) | No public DNS name and no Tailscale. Caddy runs its own certificate authority; install its root certificate on the lab's machines once: `docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > biomanager-root.crt` |
| `files` | IT issued a certificate. Put it in `deploy/certs/server.crt` and `server.key`. |
| `acme` | The name is in public DNS and port 80 is reachable, so Let's Encrypt can issue one for `ACME_EMAIL`. |

### Reaching it over Tailscale

The server joins your Tailscale network (`sudo tailscale up --hostname=biomanager`),
and in the admin console you **disable key expiry** for it (otherwise it drops
off after 180 days) and turn on MagicDNS and HTTPS. Lab members either join
the tailnet (free for up to 6 users) or get the one machine **shared** with
their own Tailscale account (Machines → biomanager → Share). The cloud
firewall then needs no inbound rules at all: not for 80/443, and not for SSH,
which also goes over Tailscale.

## Signing in with Google or Microsoft

Optional. People can then sign in with an account they already have instead
of a BioManager password. New accounts still wait for an admin's approval,
and anyone who already has an account connects Google or Microsoft from
**Settings → Sign-in methods**. How it works: `app/oidc.py`.

The provider has to know about the server first. Register it once, then put
the two values in `.env` and run `docker compose up -d`.

**Google** (console.cloud.google.com):

1. Create a project (for example "BioManager"), then go to **APIs & Services →
   OAuth consent screen**. Choose **External**, add the app name and your
   email, and leave the scopes at the defaults (`openid`, `email`, `profile`).
   While the app is in *Testing*, only the test users you list can sign in;
   either add each lab member there, or **Publish** the app. These scopes
   don't need Google's verification.
2. **Credentials → Create credentials → OAuth client ID**, type **Web
   application**, with the authorised redirect URI
   `https://DOMAIN/auth/google/callback`.
3. Copy the client ID and secret into `BIOMANAGER_GOOGLE_CLIENT_ID` and
   `BIOMANAGER_GOOGLE_CLIENT_SECRET`.

**Microsoft** (entra.microsoft.com, or portal.azure.com → Microsoft Entra ID):

1. **App registrations → New registration.** Pick who may sign in: accounts
   in any organisation and personal Microsoft accounts (tenant `common`), or
   only your organisation (then set `BIOMANAGER_MICROSOFT_TENANT` to its
   tenant ID). Redirect URI: platform **Web**, `https://DOMAIN/auth/microsoft/callback`.
2. **Certificates & secrets → New client secret.** Note when it expires and
   put a reminder in your calendar: sign-in with Microsoft stops working that day.
3. Copy the **Application (client) ID** and the secret's **Value** into
   `BIOMANAGER_MICROSOFT_CLIENT_ID` and `BIOMANAGER_MICROSOFT_CLIENT_SECRET`.

A university or hospital tenant may not let you register apps yourself;
then IT registers it with the same redirect URI.

**Your institution's sign-in** (Okta, Keycloak, Azure AD, Google Workspace,
or Shibboleth with its OpenID Connect plugin): ask IT for an OpenID Connect
client with the redirect URI `https://DOMAIN/auth/institution/callback`
and the scopes `openid email profile`. Put its issuer address in
`BIOMANAGER_OIDC_ISSUER`, the client ID and secret in
`BIOMANAGER_OIDC_CLIENT_ID` and `BIOMANAGER_OIDC_CLIENT_SECRET`, and what
the button should say (the name people know it by, e.g. CampusKey) in
`BIOMANAGER_OIDC_NAME`.

**A university that only speaks SAML** (Shibboleth, InCommon, eduGAIN):
register at cilogon.org/oauth2/register (free for research and education)
with the redirect URI `https://DOMAIN/auth/cilogon/callback` and the scopes
`openid email profile org.cilogon.userinfo`. CILogon lets people sign in
with their university account and hands BioManager an OpenID Connect
token. Put the client ID and secret in `BIOMANAGER_CILOGON_CLIENT_ID` and
`BIOMANAGER_CILOGON_CLIENT_SECRET`; set `BIOMANAGER_CILOGON_IDP` to the
university's entityID to skip CILogon's list of institutions.

Either way, as with Google and Microsoft, a new person's first sign-in is
a request an admin approves, and accounts are matched by the provider's
own identifier, never by email.

## Moving an existing lab onto the server

The SQLite database from a laptop or the desktop app is copied once,
**before the app's first start** (the app seeds an empty database when it
starts, and the copy only goes into an empty one). Instead of
`docker compose up -d --build` above:

```bash
docker compose build
docker compose up -d db
docker compose run --rm --no-deps -v /path/to/biomanager.db:/import/lab.db:ro app \
  sh -c 'python scripts/migrate-to-postgres.py /import/lab.db "$DATABASE_URL"'
docker compose up -d
```

`scripts/migrate-to-postgres.py` only reads the SQLite file. It checks that
every value fits before it writes anything, copies everything in one
transaction, keeps ids from ever being reused, and compares row counts.
`--dry-run` does all of that and then rolls back.

Uploaded files are separate. Copy them into the app volume:

```bash
docker compose cp /path/to/uploads/. app:/data/uploads/
```

The desktop app keeps them in `~/Library/Application Support/Biomanager/uploads/`;
a source checkout keeps them in `app/static/uploads/`.

## Backups

The `backup` service runs nightly at `BACKUP_TIME`, and once when it starts:

1. `pg_dump` of the database, checked with `pg_restore --list` before it is kept.
2. A tarball of the app volume: uploads, and the signing key that decrypts
   stored Google tokens.
3. The newest `KEEP_LOCAL` of each are kept in `BACKUP_DIR` (default
   `deploy/backups/`). Put that on a different disk if you can.
4. **Off-site**, if `RESTIC_REPOSITORY` is set: encrypted before it leaves,
   and kept for 30 days, 12 weeks and 24 months. Keep `RESTIC_PASSWORD`
   somewhere other than this server.
5. It pings `HEALTHCHECK_PING_URL`, if set, after every good backup. A
   service like healthchecks.io then tells you when backups **stop** — a
   failed backup can't send its own alert.

Every `RESTORE_TEST_WEEKDAY` the newest backup is restored into a scratch
database and checked. `docker compose ps` shows the backup service as
*unhealthy* when the last good backup is more than 26 hours old.

```bash
docker compose logs backup          # what ran, what it kept, restore-test results
docker compose exec backup backup.sh        # a backup now
docker compose exec backup restore-test.sh  # a restore test now
```

### Off-site copies (Backblaze B2)

The copies on this server (and on an admin's Mac, below) do not survive losing both.
An off-site copy in Backblaze B2 does, and costs nothing at this size (the
first 10 GB are free; a year of nightly backups is well under 1 GB).

1. Create a Backblaze account at backblaze.com (B2 Cloud Storage).
2. **Buckets → Create a Bucket**: a unique name (for example
   `biomanager-yourlab-offsite`), Files **Private**, Default Encryption
   **Enable**, Object Lock **Enable**. Then, in the bucket's settings, set
   the default Object Lock retention to **Governance, 30 days**. With it, no
   one who takes over the server can delete or overwrite the copies of the
   last 30 days, even with its key.
3. On the bucket's page, note the **Endpoint**, such as `s3.us-east-005.backblazeb2.com`.
4. **Application Keys → Add a New Application Key**: name `biomanager-server`,
   access to **this bucket only**, **Read and Write**. Keep the page open: the
   `applicationKey` is shown only once.
5. On the server, in your own terminal (over SSH, `ssh -t <server> sudo …`):

   ```bash
   sudo /opt/biomanager/Biomanager/deploy/host/offsite-setup.sh
   ```

   Repository: `s3:https://<endpoint>/<bucket>/biomanager`. Paste the keyID
   and the applicationKey when asked (the key is not shown as you paste).
   It shows the backup password once: **save it in your password manager**.
   Then it takes a backup, sends it off-site and restores it back to prove
   the path works.

From then on every night's backup goes off-site too (kept 30 days, 12 weeks,
24 months), a fifth of the stored data is read back every Sunday with the
latest backup restored from off-site, and the watchdog alerts **offsite**
if a night's copy fails, **offsite-restore-test** if the weekly read-back
stops passing. A failed off-site copy never counts against the local backup.

Other S3-compatible storage (AWS S3, Wasabi, a university's S3 service)
works the same way with its own endpoint.

### A copy on a Mac

An admin's Mac can pull the server's backups every night at 03:15 into
`~/BioManagerBackups`, checking each new dump is readable. It never deletes
a copy because the server did, so a damaged server can't empty it. It needs
`ssh <server>` to work without a password prompt, and passwordless `sudo`
on the server (the backups are root's). On the Mac, from a copy of `deploy/`:

```bash
BIOMANAGER_SSH_HOST=<server> BIOMANAGER_SERVER_BACKUPS=<BACKUP_DIR on the server> deploy/mac/install.sh
```

`BACKUP_DIR` is the one in `.env`, as a full path (for the default,
`/opt/biomanager/Biomanager/deploy/backups`). A macOS notification says
when a night's copy fails, or when the server's newest backup is over two
days old. `deploy/mac/install.sh --remove` stops it; the copies stay.

### Restoring

The backup files belong to root and only root can read them (they hold the
signing key), so list them through the backup service:

```bash
docker compose exec backup ls -lt /backups/db /backups/files
```

```bash
docker compose stop app
docker compose --profile restore run --rm restore \
  /backups/db/biomanager-<time>.dump /backups/files/biomanager-files-<time>.tar.gz
docker compose start app
```

Nothing is deleted. The database being replaced is renamed
`biomanager_before_<time>`, and the files being replaced are moved into
`.before-restore-<time>/` on the volume. Drop those once you are sure.

From the off-site copy, first `restic snapshots` to pick the copy by its
time, then `restic restore <ID> --target /somewhere` with the same
repository and password, and restore from those files (RUNBOOK: The server
is lost).

## Alerts

The watchdog checks the site, the services, the disk, the backups and
their restore tests every 5 minutes, a weekly job refreshes the images
the stack is built on (a backup first), and an updater waits for **Update
now** in the app (`host/update.sh`, below). On the server:

```bash
sudo /opt/biomanager/Biomanager/deploy/host/install.sh
```

It makes a private [ntfy](https://ntfy.sh) topic and prints its name:
subscribe to it in the ntfy app on your phone. Alerts say what is wrong,
never any lab data. [RUNBOOK.md](RUNBOOK.md) says what to do for each.

## Updating

The app looks for a new release once a day (it sends only its version;
`BIOMANAGER_UPDATE_CHECK=0` in `.env` stops it) and tells its admins. With
the server bundle and `host/install.sh` run, **Update now** in **Settings →
Devices & copies → Updates** takes a backup, downloads the newest release's
bundle and image, checks both against the SHA-256 GitHub publishes, loads
the image, unpacks the bundle over this one and restarts, and the page
follows it. The app asks through `deploy/control/` (mounted at `/control`),
which `install.sh` makes root's and sticky: the app can leave a request
there and nothing else, and `update.sh` reads nothing from it but that it
is there, so the most it can bring about is BioManager's own newest
release. `sudo host/update.sh` does the same from a shell. By hand:

```bash
docker compose exec backup backup.sh     # a fresh backup first
git pull                                 # a checkout; with the bundle, unpack the newer one over it
host/load-image.sh                       # the bundle only: load that version's image
docker compose up -d --build
docker compose logs -f app               # watch it start
```

Start-up brings the database schema up to date by itself.

## Without Docker

Everything above also works on a plain server:

- the app runs with `gunicorn -c gunicorn.conf.py wsgi:app` behind Caddy or nginx (see the main README);
- `deploy/backup/backup.sh` and `restore-test.sh` run from cron with the
  usual `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`, `APPDATA_DIR` and `BACKUP_ROOT` set.
