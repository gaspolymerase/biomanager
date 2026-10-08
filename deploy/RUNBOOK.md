# BioManager operations runbook

What to do when something happens on your lab's BioManager server. Each
section is a checklist with the commands to run. Setting the server up in
the first place is in [README.md](README.md).

Commands marked **server** run on the server, in the deploy folder:

```bash
ssh <your server>
cd /opt/biomanager/Biomanager/deploy
```

## Where everything is

Fill in the right-hand column once, for your server, and keep a copy
somewhere other than the server (a password manager note works well).

| What | Where | Yours |
| --- | --- | --- |
| The site | `https://DOMAIN`, the `DOMAIN` in `.env` | |
| The server | the machine or cloud VM, and how you sign in to it (SSH, a console) | |
| Its provider | where you start, stop or rebuild it: a cloud console, or who in IT looks after it | |
| The code | `/opt/biomanager/Biomanager`: a server bundle from a release, or a checkout of the source | |
| Settings and secrets | `/opt/biomanager/Biomanager/deploy/.env` (readable only by you) | |
| Services | `db` (PostgreSQL 16), `app` (gunicorn), `caddy` (HTTPS), `backup` | |
| Backups on the server | `BACKUP_DIR` in `.env` (`deploy/backups/` unless set): nightly at `BACKUP_TIME`, `KEEP_LOCAL` kept, restore-tested each `RESTORE_TEST_WEEKDAY` | |
| Backups elsewhere | off-site with restic, if `RESTIC_REPOSITORY` is set (its password is in your password manager, not only in `.env`); a copy on an admin's Mac, if `deploy/mac/install.sh` was run (it pulls from `/opt/biomanager/backups`; set `BIOMANAGER_SERVER_BACKUPS` to your `BACKUP_DIR` when installing it if yours is elsewhere) | |
| Alerts | the ntfy topic in `/etc/biomanager/watchdog.env`, if `sudo deploy/host/install.sh` was run; macOS notifications from the Mac's copy | |
| Automatic jobs | with `deploy/host/install.sh`: a check every 5 minutes, the images refreshed on Sundays at 03:30, and the updater that **Update now** in Settings starts (`host/update.sh`). The app looks for a new release once a day and tells its admins. With `cloud-init.yaml`: operating system security updates nightly, restarting at 04:30 if they need to | |

## An alert arrived

These come from the watchdog (`deploy/host/install.sh`). Without it, look at
`docker compose ps` now and then, and use a service such as healthchecks.io
(`HEALTHCHECK_PING_URL`) to hear when backups stop.

| Alert | What it means | Do |
| --- | --- | --- |
| **site** | `/healthz` does not answer over HTTPS | [The site is down](#the-site-is-down) |
| **containers** | a service stopped or is unhealthy | **server** `docker compose ps`, then `docker compose logs --tail 100 <service>`; `docker compose up -d` restarts anything stopped |
| **disk** | the disk is over 85% full | **server** `df -h /`, `docker system df`; `docker image prune -f` frees old images. If backups grew, lower `KEEP_LOCAL` in `.env` |
| **backup** | no good backup for 26 hours | **server** `docker compose logs backup`; run one now with `docker compose exec backup backup.sh` |
| **restore-test** | the weekly restore test has not passed for 8 days | **server** `docker compose exec backup restore-test.sh` and read what it says. Treat this as urgent: the backups may not be restorable |
| **offsite** | last night's off-site copy failed (the local backup is fine) | **server** `docker compose logs backup` names the reason: no answer from the storage, a wrong password, or a key that may not write. Run one now: `docker compose exec backup backup.sh` |
| **offsite-restore-test** | the weekly read-back from off-site has not passed for 8 days | **server** `docker compose exec backup restore-test.sh`. Urgent: the off-site copies may not be restorable |
| **certificate** | the HTTPS certificate expires within 14 days | With `TLS=internal` or `acme`, Caddy renews it: **server** `docker compose logs caddy` says why it didn't. With `tailscale`, check HTTPS is still on in the Tailscale admin console (DNS). With `files`, put IT's new one in `deploy/certs/`. Then **server** `docker compose restart caddy` |
| **tailscale** | the server left the Tailscale network | **server** (through the provider's console if SSH is down) `sudo tailscale up` |
| **weekly update failed** | Sunday's image refresh stopped | Read the message. Nothing was updated if the backup failed. If the app is unhealthy after it, see [Updating went wrong](#updating-went-wrong) |
| **update failed** | **Update now** stopped | Read the message: it names the step. Before the restart nothing changed (the old version still runs); after it, see [Updating went wrong](#updating-went-wrong). The same text is in Settings → Devices & copies, and `journalctl -u biomanager-update` has the rest |
| **updated to …** | **Update now** finished, and the app is healthy | Nothing |
| Mac: **backup copy failed** | the Mac could not pull from the server | Is the Mac on the lab's network (or Tailscale)? Then `ssh <your server>` by hand; if that fails, the server is down |
| Mac: **backups have stopped** | the server's newest backup is over 48 h old | The server is up but not backing up: as **backup** above |

## The site is down

1. On your own device: are you on the network that reaches the server (the
   campus network, the VPN, Tailscale switched on)? Open `https://DOMAIN/healthz`;
   it answers `ok`.
2. Sign in to the server. If you can't, the machine or its network is down;
   go to step 5.
3. **server** `docker compose ps`. Start anything not running: `docker compose up -d`.
4. **server** `docker compose logs --tail 200 app` (or `caddy`, `db`) for the error.
5. In the provider's console (or ask IT), is the machine running? If it is
   stopped, start it. Everything starts by itself at boot: Docker, the stack,
   Tailscale and the timers. Give it three minutes.
6. If the machine is gone, or will not boot: [The server is lost](#the-server-is-lost).

Free cloud VMs that look idle may be stopped by the provider (Oracle's
Always Free tier does this after a week). Starting it again loses nothing;
a paid account, even one that stays within the free resources, stops it
happening.

## Restore a backup (the server is fine)

For a mistake in the data that undo in the app cannot fix. Everything since
that backup is lost, so check the time first.

```bash
# server
docker compose exec backup ls -lt /backups/db /backups/files   # pick a time
docker compose stop app
docker compose --profile restore run --rm restore \
  /backups/db/biomanager-<time>.dump /backups/files/biomanager-files-<time>.tar.gz
docker compose start app
```

The replaced database is kept as `biomanager_before_<time>` and the replaced
files in `/data/.before-restore-<time>/`. When you are sure:

```bash
# server
docker compose exec db psql -U biomanager -d postgres -c 'DROP DATABASE "biomanager_before_<time>"'
```

To check a backup without replacing anything, run a restore test on it:
`docker compose exec backup restore-test.sh /backups/db/<file>.dump` restores
that one (with no file named, the newest) into a scratch database, checks it
and drops it again.

## The server is lost

The machine was deleted, or its disk is unreadable. Rebuild from a copy of
the backups that was not on it: off-site, or an admin's Mac.

1. Set up a new server as in [README.md](README.md) (for a cloud VM,
   `cloud-init.yaml` does the first steps).
2. Give it the same name people use. With Tailscale:
   `sudo tailscale up --hostname=biomanager`, after **deleting the old
   machine** in the Tailscale admin console so the new one gets the name;
   disable its key expiry. Otherwise point the DNS name at the new machine.
   On your own computer, forget the old machine's SSH key:
   `ssh-keygen -R <its name>`.
3. **server** put the same version of BioManager in place as before (the
   bundle from the release you ran, or the checkout), and write `.env`
   again: the same `DOMAIN`, a new `POSTGRES_PASSWORD`, and `SECRET_KEY`
   left empty (the restored files bring the old key back).
4. **server** `docker compose up -d` (`--build` for a checkout), wait for it to be healthy, then restore
   the newest backup.

   From an admin's Mac:

   ```bash
   # Mac
   scp ~/BioManagerBackups/db/<newest>.dump ~/BioManagerBackups/files/<newest>.tar.gz <your server>:/tmp/
   # server: into BACKUP_DIR (see .env), which the backup service sees as /backups
   sudo mv /tmp/biomanager-*.dump <BACKUP_DIR>/db/
   sudo mv /tmp/biomanager-files-*.tar.gz <BACKUP_DIR>/files/
   docker compose stop app
   docker compose --profile restore run --rm restore /backups/db/<newest>.dump /backups/files/<newest>.tar.gz
   docker compose start app
   ```

   From off-site: put the same `RESTIC_REPOSITORY`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` and `RESTIC_PASSWORD` (from your password
   manager) in the new `.env`, then:

   Pick the copy by its time, not "latest": `restic snapshots` lists them
   with the time each was taken. Take the newest one from **before** the
   old server was lost. (A server with no accounts yet sends nothing
   off-site, but a copy made by the new server after someone signed up on
   it would be newer than the one you want.)

   ```bash
   # server
   docker compose up -d --force-recreate backup
   docker compose exec backup restic snapshots --tag biomanager   # ID, time and files of each copy
   docker compose exec backup restic restore <ID> --target /backups/from-offsite
   docker compose exec backup sh -c 'ls /backups/from-offsite/backups/db /backups/from-offsite/backups/files'
   docker compose stop app
   docker compose --profile restore run --rm restore \
     /backups/from-offsite/backups/db/<name>.dump /backups/from-offsite/backups/files/<name>.tar.gz
   docker compose start app
   ```
5. **server** `sudo host/install.sh` for the watchdog and weekly
   update. It keeps the old ntfy topic only if you copy
   `/etc/biomanager/watchdog.env` back; otherwise subscribe to the new one.
6. Check you can sign in, then run a restore test: `docker compose exec backup restore-test.sh`.

## The app says it is read only

"Read only: the lab's master copy is now on …" means an admin handed the
lab's master copy to a desktop (Settings → Devices): people work there, and
this server shows the lab as it was. It comes back when that desktop uses
**Give the master copy back**. If that computer is lost, an admin here
chooses **Make this the master again** under Settings → Devices (what was
changed on it since is not here). "…is moving to …" lasts a minute while the
desktop takes its last copy; if it stays, **Cancel the hand-over** there.
Taking the copy back first keeps what the server had, in the app's own
volume: `docker compose exec app ls -lt /data/backups` lists the
`before-taking-back-<time>.db` files (a copy in the desktop app's format).

## Moving to another server

The same as [The server is lost](#the-server-is-lost), but first take a
fresh backup (`docker compose exec backup backup.sh`), copy it off the old
server (an admin's Mac: `~/Library/Application\ Support/BioManager/pull-backups.sh`),
and stop the old app (`docker compose stop app`) so nobody writes to it meanwhile.

## An admin is locked out

- Another admin resets their password in **Settings → People & access** (the **···** beside them).
- No other admin can sign in:

  ```bash
  # server
  docker compose exec app python scripts/reset-password.py            # list accounts
  docker compose exec app python scripts/reset-password.py NAME --admin --enable
  ```

  It asks for the new password twice; nothing lands in the shell history.

## Someone joins

1. Make sure they can reach the server: on the campus network or VPN, or,
   with Tailscale, admin console → Machines → the server → **Share**, and
   send them the invite (sharing gives them this machine only).
2. Send them the address, `https://DOMAIN`, and the user guide.
3. When they sign up, approve them in **Settings → People & access**. Give
   animal-facility staff the *Animal care* or *Facility manager* role there.

## Someone leaves

1. **Settings → People & access** → **···** beside them → **Disable the account**. They are signed out at once.
2. Hand their records to someone: on the **Mice** and **Cages** sheets, show
   everyone's, filter by their name, tick them and use **Set → Owner** (**Settings →
   Statistics** shows who holds what). Do the same
   in the other databases they used.
3. Take away their network access: revoke the Tailscale share (or remove
   them from the tailnet), or ask IT to.
4. If they were an admin, check who else is. Keep at least two.

## Updating the app

The server looks for a new release once a day and tells its admins. With the
**server bundle** and `host/install.sh` run, an admin presses **Update now**
in **Settings → Devices & copies → Updates**: it does everything below
(`host/update.sh`, as root, through `biomanager-update.path`), checks the
bundle and the image against the SHA-256 GitHub publishes, and the page
follows it to the end. It always installs the latest release GitHub lists,
and only when it is newer. By hand, the same is `sudo host/update.sh`, or:

```bash
# server
docker compose exec backup backup.sh     # a fresh backup first
```

With the **server bundle**, download the newer release's
`biomanager-server.tar.gz` and unpack it over this one (`.env`, backups and
certificates are not in it, so they are kept):

```bash
tar -xzf biomanager-server.tar.gz -C /opt/biomanager
host/load-image.sh
docker compose up -d --build
```

Then `sudo host/install.sh`, once, if this version is the first with
**Update now**: it sets up the updater (and `deploy/control/`, where the app
asks for it).

With a **checkout**: `git pull`, then `docker compose up -d --build`.
(`--build` rebuilds the backup service, so updated backup and restore
scripts are used; it takes no second backup while the one above is fresh.)

Then check it: `curl -fsS https://DOMAIN/healthz` answers `ok`, and
`docker compose logs app` shows it started. Start-up brings the database
schema up to date by itself; when it does, the log says
`Upgrading the database from … to …`.

### Updating went wrong

If **Update now** stopped before **Restarting the server**, nothing changed:
the old version still runs; fix what the message says and press it again.
After that step, read on.

The backup taken just before is the newest in `/backups/db` (the backup
service takes none of its own at start while one from the last 12 hours is
there). Put the
previous version back: unpack the previous release's bundle and run
`host/load-image.sh`, or `git checkout` the previous tag, then
`docker compose up -d` (`--build` for a checkout).

If the log said `Upgrading the database`, the previous version can't open
the upgraded database: also [restore](#restore-a-backup-the-server-is-fine)
the backup taken just before the update. Anything entered since then is
lost, so do it soon, and tell the lab.

## Letting a guest in from the internet

For someone outside the lab (a collaborator, a visitor taking a look), for
a few days. It needs Tailscale (`TLS=tailscale`).

1. In BioManager: account menu → **Guests**. Enter who it is for and how
   long, then **Make a code**. The code is shown once; the guest gets a
   member account of their own that stops working when the pass ends.
2. On the server, put BioManager on the internet (Tailscale Funnel):

   ```bash
   # server
   sudo host/internet-access.sh on
   ```

   The first time, Tailscale prints a link to allow Funnel for this machine:
   open it, allow it, run the command again.
3. Send the guest `https://DOMAIN:8443/guest` and the code. From the
   internet, anyone not signed in sees only that code page: no sign-in or
   sign-up form.
4. When they are done: **End now** on the Guests page, and

   ```bash
   # server
   sudo host/internet-access.sh off
   ```

What a guest adds stays, under their `guest-…` account.

Internet access also lets people connect claude.ai, the Claude apps or
ChatGPT to BioManager as a custom connector, at
`https://DOMAIN:8443/api/v1/mcp`: those apps' servers call it from the
internet. A connector signs in with a connection code each person makes on
the lab network (**Settings → API tokens → Connect an AI assistant**), and
can only read and propose changes that person approves. Turning internet
access off disconnects them until it is on again; Claude Code, Cursor and
the other apps that run on a lab computer don't need it.

## Rotating secrets

| Secret | How | What people notice |
| --- | --- | --- |
| `SECRET_KEY` (or `/data/secret_key`) | set a new value in `.env`, `docker compose up -d` | everyone signs in again; Google Calendar links must be reconnected |
| `POSTGRES_PASSWORD` | **server** `docker compose exec db psql -U biomanager -d postgres -c "ALTER USER biomanager PASSWORD '<new>'"`, put `<new>` in `.env`, `docker compose up -d` | nothing |
| Microsoft client secret | it expires on the date shown in Entra; create a new one, put it in `.env`, `docker compose up -d` before the old one expires | "Sign in with Microsoft" fails after expiry |
| Google client secret | Google Cloud → Credentials → the client → add a secret, update `.env`, then delete the old one | nothing |
| Institution sign-in secret | IT issues a new one; update `.env`, `docker compose up -d` | nothing, if done before the old one stops working |
| ntfy topic | edit `/etc/biomanager/watchdog.env` on the server, subscribe to the new topic | nothing |
| Off-site storage key | create a new key (this bucket, read and write), **server** `sudo host/offsite-setup.sh` again with it, then delete the old key | nothing |
| Backup password | not rotated in place: it encrypts every stored copy. To change it: `docker compose exec backup restic key add` (it asks for the new one), put the new one in `.env` and your password manager, `docker compose up -d`, then `docker compose exec backup restic key list` and `restic key remove <ID>` the old one (restic won't remove the key it is using) | nothing |
| An admin's SSH key | make a new key, put its `.pub` in the server's `~/.ssh/authorized_keys`, remove the old line | nothing |

## Checking on it by hand

```bash
curl -fsS https://DOMAIN/healthz                                  # the app and database
# server
docker compose ps
sudo journalctl -u biomanager-watchdog --since today --no-pager -o cat
systemctl list-timers "biomanager-*" --no-pager
# Mac, if it keeps a copy
tail ~/BioManagerBackups/pull.log
```
