# Installing il2ks with Docker

For a **Linux** machine (typically one that runs DServer under Wine) that has Docker with the Compose plugin. One
container runs everything: the web server, the log watcher and Caddy (HTTPS). The database is SQLite in a Docker
volume. On Windows, the [normal install](install.md) is the recommended way (Docker Desktop is often unavailable on
rented Windows servers); Docker Desktop on a Windows PC works too, see [Windows hosts](#windows-hosts-docker-desktop).

**You need:** Docker with `docker compose`, a **domain name** pointing at this machine (like `stats.example.com`),
**ports 80 and 443** free and reachable from the internet (otherwise [use your own proxy](#behind-your-own-proxy)), and
DServer's **text mission logs** switched on. Expect 10 minutes plus the wait for DNS and the certificate.

## 1. Get it and configure it

```bash
git clone https://github.com/FGlazov/il2_korea_stats.git
cd il2_korea_stats
cp docker/.env.example docker/.env
nano docker/.env
```

Set at least these (the file explains every line):

| Setting | What |
|---|---|
| `IL2KS_HTTPS_DOMAIN` | your domain. Empty gives a self-signed test certificate (browsers warn) |
| `IL2KS_SERVER_TIMEZONE` | the time zone of the game server, like `Europe/Berlin` or `UTC`. DServer names its logs in its local time |
| `IL2KS_LOGS_HOST_DIR` | DServer's text log folder on this machine, the one holding `missionReport(...)[0].txt` ([Wine](#dserver-under-wine)) |
| `IL2KS_ADMIN_USERNAME`, `IL2KS_ADMIN_PASSWORD` | the admin account, created on the first start |

There is no browser setup page in Docker (it only opens for a browser on the same machine, which a container's never is).
If you leave the admin variables out, the container log says so in a framed notice and shows the command to run instead:
`docker compose -f docker/compose.yaml exec il2ks il2ks createadmin`. After the first start the password is removed from
the server's own environment; only Docker's configuration (`docker/.env`, `docker inspect`) still holds it, so protect that
file. Or use a Docker secret: add `IL2KS_ADMIN_PASSWORD_FILE` (the path of the secret file inside the container) under
`environment:` in `docker/compose.yaml` instead of the password.

## 2. Start it

```bash
docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml logs -f        # Ctrl+C leaves it running
```

After a minute, open `https://stats.example.com`. The admin area is at `/admin/`. The container restarts after crashes
and after a reboot (`restart: unless-stopped`; Docker itself must start at boot: `sudo systemctl enable docker`).

Check the setup any time (it reads the same settings, no changes made):

```bash
docker compose -f docker/compose.yaml run --rm il2ks doctor
```

(`doctor` may complain about ports 80/443 while the site runs: that is itself. It reports the rest correctly.)

To avoid typing `-f docker/compose.yaml` every time: `cd docker` first, or `export COMPOSE_FILE=docker/compose.yaml`.

## Where things live

- **`/data`** in the container is the Docker volume `il2ks_il2ks-data`: the database, secret key, certificates, backups,
  `custom/` and the archived mission logs. Keep it. `docker compose down` keeps it; **`docker compose down -v` deletes it.**
- **`/logs`** is DServer's folder, mounted **read-only** by default. il2ks reads it and never changes it
  (`IL2KS_LOGS_AFTER_ARCHIVE=keep`, so the originals stay where DServer wrote them). To let il2ks move them away after
  archiving, as in the normal install, set `IL2KS_LOGS_READ_ONLY=false` and `IL2KS_LOGS_AFTER_ARCHIVE=move` in `.env`
  (see [file permissions](#file-permissions)).

## DServer under Wine

The logs sit inside the Wine prefix, for example
`/home/you/.wine/drive_c/Program Files/IL-2 Sturmovik Great Battles/data/...`. Find the folder:

```bash
find ~/.wine -name 'missionReport*' | head -3        # or the path of your own WINEPREFIX
```

Put its folder in `docker/.env` as a plain absolute path (no `~`):

```
IL2KS_LOGS_HOST_DIR=/home/you/.wine/drive_c/Program Files/IL-2 Sturmovik Great Battles/data/Multiplayer/Cooperative/Logs
```

If the folder does not exist yet because no mission has run, Docker refuses to start (on purpose: a typo should not
silently watch an empty folder). Create it or start the mission first. The time zone is the one of the **Linux host**
that runs Wine (`timedatectl`), as Wine passes it on.

## File permissions

The container runs as user **1000:1000**, not root. For the default read-only mount this only matters if DServer's files
are not readable by others (`ls -l` shows `r--` for "others" on files and `r-x` on folders when they are). If they are
not:

- Set `IL2KS_CONTAINER_UID` and `IL2KS_CONTAINER_GID` in `.env` to the owner of the prefix (`id -u`, `id -g`), **and**
  recreate the data volume if it already exists (`docker compose down -v` before the first real data, because the volume
  keeps the owner it was created with), or
- `chmod -R o+rX` the log folder.

If you let il2ks move or delete logs (`IL2KS_LOGS_READ_ONLY=false`), the container user must be allowed to write to that
folder: same user ID as the owner, or `chmod o+w`. Rootless Docker and Podman map IDs differently (your own user is
`0` inside); then leave the ID at 1000 and use `podman unshare chown` / `--userns=keep-id` as that tool documents.

## A map or other image for the front page

The admin can show a large image first on the front page (Site settings, Front page image) read from a file path. In a
container that path is **inside the container**: mount the file (or its folder) as an extra volume in your compose file,
read-only is enough, and enter the container's path in the admin. The container user (1000:1000, see above) must be able
to read it. See [customizing.md](customizing.md#a-large-image-on-the-front-page).

## Windows hosts (Docker Desktop)

Tested with Docker Desktop for Windows (WSL 2 backend, Linux containers), from PowerShell and Git Bash:

- **Log folder:** a normal Windows path in `docker/.env`, no quotes, for example
  `IL2KS_LOGS_HOST_DIR=C:\Users\you\Documents\IL-2\data\Multiplayer\Cooperative\Logs` (forward slashes work too). The
  drive must be shared with Docker Desktop (local drives are by default). The folder must exist, as on Linux. A network
  share (`\\server\...`) is not supported: run il2ks on the machine that has the logs.
- **New logs are noticed** although Windows bind mounts send no file-change events: the watcher polls the folder every
  `[ingest] watch_interval_s` (30 s by default) and does not depend on such events.
- **Permissions:** Docker Desktop shows Windows files as readable by everyone and writable by any user, so
  `IL2KS_CONTAINER_UID`/`GID` and [file permissions](#file-permissions) do not matter. `IL2KS_LOGS_READ_ONLY=false` with
  `IL2KS_LOGS_AFTER_ARCHIVE=move` works.
- **Ports 80 and 443** must be free on Windows (IIS, other web servers and some VPN or sharing software take them:
  `netstat -ano | findstr ":443 "` shows who). Windows also reserves port ranges (`netsh interface ipv4 show
  excludedportrange protocol=tcp`); if 80 or 443 lies inside one, use [your own proxy](#behind-your-own-proxy). On the same
  PC the site is at `https://localhost` (self-signed test certificate, the browser warns). A real certificate needs the PC
  to be reachable from the internet on port 80, which behind a home router means forwarding it.
- **Line endings:** the repository's `.gitattributes` keeps `*.sh` at LF, so `docker/docker-entrypoint.sh` works after a
  Windows checkout with `core.autocrlf=true`. If an editor saved it with CRLF, the container fails with
  `exec ... no such file or directory`; convert it back to LF.
- **Startup is slower** than on Linux (the first start takes up to a minute). Docker Desktop must be running; turn on
  "Start Docker Desktop when you sign in" so that the container (`restart: unless-stopped`) comes back after a reboot.
- The smoke test runs on Windows too: `bash docker/smoke-test.sh` from Git Bash.

## Day to day

```bash
docker compose -f docker/compose.yaml exec il2ks il2ks createadmin     # another admin, or reset a password
docker compose -f docker/compose.yaml exec il2ks il2ks backup          # zip in /data/backups
docker compose -f docker/compose.yaml cp il2ks:/data/backups ./il2ks-backups   # get them out of the volume
docker compose -f docker/compose.yaml restart                           # after a change in il2ks.toml; after changing .env use `up -d`
docker compose -f docker/compose.yaml exec il2ks il2ks reprocess --all # after changing a rule: see settings.md
```

Backups also happen automatically every day and before each upgrade (`il2ks backup` has the details). Copy them
somewhere else from time to time.

**Upgrade:**

```bash
git pull
docker compose -f docker/compose.yaml up -d --build
```

The database is migrated at start, after an automatic backup, and what the new version needs is filled in from your data
(a few minutes on a big database). The volume is untouched. Old missions are not recalculated by themselves: if the release
notes say a rule or score changed, run `il2ks reprocess --all` or `il2ks rebuild-aggregates` as above. See
[Rules, scoring and tours](settings.md#what-to-run-after-a-change).

**Change a setting** that has no line in `.env`: every `il2ks.toml` setting can be given as an environment variable
`IL2KS_<SECTION>_<KEY>` (`[web] threads` is `IL2KS_WEB_THREADS`); add it under `environment:` in `docker/compose.yaml`.
Or put an `il2ks.toml` into the volume: `docker compose cp il2ks.toml il2ks:/data/il2ks.toml` (environment variables
still win over it).

## Behind your own proxy

If 80 and 443 are taken (nginx, Traefik, ...), let that proxy do HTTPS and use **external mode**
([reverse-proxy.md](reverse-proxy.md) says what the proxy must send). In `docker/compose.yaml` replace the `ports:` with
`"127.0.0.1:8000:8000"` and add these under `environment:`:

```yaml
      IL2KS_HTTPS_MODE: external
      IL2KS_WEB_HOST: 0.0.0.0     # inside the container; the port is published on 127.0.0.1 only
```

## Troubleshooting

| What you see | Likely reason and fix |
|---|---|
| `set IL2KS_SERVER_TIMEZONE in docker/.env` or `set IL2KS_LOGS_HOST_DIR ...` | `docker/.env` is missing or incomplete; run compose from the folder that has it, or use `-f docker/compose.yaml`. |
| `bind source path does not exist` | The log folder in `IL2KS_LOGS_HOST_DIR` is wrong or not created yet. |
| `Bind for 0.0.0.0:80 failed: port is already allocated`, or on Windows `ports are not available` | Something else uses 80 or 443: [your own proxy](#behind-your-own-proxy). |
| Browser says "not secure" | `IL2KS_HTTPS_DOMAIN` is empty (test certificate), or the real certificate is not issued yet: DNS must point here and port 80 be reachable from outside. `docker compose logs il2ks` shows lines from `caddy`. |
| The site works but is empty | Wrong log folder, DServer's text logs off, or wrong permissions: `docker compose run --rm il2ks doctor`. |
| Missions are named with the wrong day | `IL2KS_SERVER_TIMEZONE` does not match the game server. |
| `docker compose ps` shows `unhealthy` | The web server does not answer: `docker compose logs il2ks`. |
| "the admin account was NOT created" in the logs | The password was too weak or too similar to the name. Change it in `.env`, `up -d`; if the site already has an admin, it is not touched. |

More: the [troubleshooting table of the normal install](install.md#troubleshooting) applies too. For issues, paste the output of
`docker compose run --rm il2ks doctor` and `docker compose logs --tail 100 il2ks`, **never** your `.env` or `secret_key.txt`.
