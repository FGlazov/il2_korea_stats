# 07 — Deployment and Installation

Pain point P2: installing `il2_stats` meant installing Postgres 9.5 by hand, creating a DB user and DB in
pgAdmin, running `CREATE EXTENSION` SQL, installing Python 3.5, editing `conf.ini`, adding settings to the game's
`startup.cfg`, running `install.cmd`, and then keeping two console windows (`stats.cmd`, `waitress.cmd`) open forever.

**Goal (NFR-INS-1..4):** a non-programmer gets a running site in under 15 minutes, both processes start
with one action and survive reboots, and upgrades keep the data.

## Target host `[DECIDED]` (2026-10-02)

- **Windows**, and the admin **has admin rights** (maintainer's call, based on the research below). Installing a Windows service,
  opening firewall ports, and binding ports 80 and 443 are all acceptable.
- **One install per game server** (FR-OPS-5). Several DServers on one machine (for example under different Windows users) is a rare
  exception, handled by side-by-side installs with separate data directories, ports, and service names.
- **The stats site can run on the game machine (normal case) or on another machine** that logs get copied to (FR-ING-16).
  Admins on managed hosts (RDP access) can install software on the game machine.
- **Linux** (Wine-hosted DServers) is a secondary target, served by the **pip/uv install path (C)** from v1. Docker (A) comes later. At least one
  Korea server admin runs Linux (2026-10-02).

### Research behind it
- DServer is a **Windows executable** (`DServer.exe`). Community guides and forum threads describe Windows 10/Pro or
  Windows Server setups ([DServer system requirements thread][r1], [Steam DServer setup guide][r2], [Server Hosting wiki][r3]).
- Linux only works through **Wine**: there's a community Docker image and a Lutris installer for Great Battles DServer
  ([Linux server thread][r4], [IL-2 DServer Docker/Linux thread, 2024][r5]). Neither mentions Korea.
- Managed hosts (for example [Fox3 Servers][r6]) give customers **RDP access**, which means Windows.
- The `il2_stats` install docs were also Windows-first.

[r1]: https://forum.il2sturmovik.com/topic/15481-dserver-system-requirements/
[r2]: https://steamcommunity.com/sharedfiles/filedetails/?id=478746156
[r3]: https://il2sturmovik.fandom.com/wiki/Server_Hosting
[r4]: https://forum.il2sturmovik.com/topic/17923-linux-server-possible/
[r5]: https://forum.il2sturmovik.com/topic/87499-il-2-dedicated-server-docker-il-2-hosting-on-linux/
[r6]: https://fox3servers.com/servers/il2

## What has to run

1. The database: **SQLite**, a file, so there's nothing to run (TD-04; Postgres is dev-side only).
2. `il2ks web` (granian serving Django, TD-10).
3. `il2ks watch` (ingester loop).

4. The HTTPS reverse proxy (bundled Caddy, TD-23), unless the admin brings their own.

`il2ks run` supervises items 2, 3 and 4 (see [04_architecture.md](04_architecture.md#process-model-proposed)).
**With SQLite, the whole system is a single thing to run: `il2ks run`, as one Windows service.** That's the main reason SQLite fits the installer.

### Remote log mode (FR-ING-16)
If the site runs on a different machine than DServer, `logs.path` points to a folder the logs arrive in: a network share (SMB) of the
DServer log folder, or a folder kept in sync by a copy tool (for example a scheduled `robocopy /MIR`). Ingestion is already
copy-tolerant (it only reads parts whose size is stable). A small `il2ks ship` helper running on the game machine may come later.

There's also one game-side step that no tool can skip: turning on text mission logs in the DServer config
(`mission_text_log = 1` and `text_log_folder` in `startup.cfg` for BoS; the Korea samples prove text logs still exist,
but the exact config keys still need checking). `il2ks doctor` should detect when they're missing and explain the fix.

## Options

### A. Docker Compose
`compose.yaml` with an `il2ks` service (`il2ks run`, SQLite in a volume), the game's log folder bind-mounted
read-only, a named volume for data, and `restart: unless-stopped`.
- ✅ Cheap to build. Reproducible. Same setup in dev and prod. Fits the Linux/Wine DServer crowd.
- ❌ Docker on Windows needs Docker Desktop with WSL2 or Hyper-V. It's often unavailable on rented Windows hosts and not supported on
  Windows Server. It's still a terminal workflow, and bind-mounting Windows paths trips people up.
- **No browser setup page in the image** `[DECIDED]` (maintainer, 2026-10-04, OQ-70): a container never sees a loopback peer, so the image sets
  `IL2KS_SETUP_PAGE=off`, the logs explain `il2ks createadmin` while no admin exists, and `docs/install-docker.md` documents it (doc 16 "Setup page").
- **`il2ks restore` refuses while the site runs** `[DECIDED]` (maintainer, 2026-10-04, OQ-71): it swaps the database, the config, `custom/`
  (template overrides), `media/`, the server ID and the secret key, so it exits with code 3 when `il2ks run` holds its lock or the web port
  answers; `--force` overrides (scripts that restored against a live site need it). Doc 16 "Backups".

### B. Native Windows installer — now much simpler with SQLite
An installer (Inno Setup or WiX) that bundles:
- **the app**: a uv-managed Python environment created at install time (`uv` is a single exe), or a frozen build
  (PyInstaller, Nuitka). Decide in a packaging spike.
- **SQLite by default**: the database is a file in `%ProgramData%\il2ks\`. No DB server, no password, no service.
- **one Windows service** for `il2ks run` (through a service wrapper such as WinSW, or `pywin32`), starting on boot.
- **Caddy** for HTTPS (TD-23), plus firewall rules for ports 80 and 443.
- a setup page that asks for the **game server folder** (auto-detected where possible), the **domain name** (for the certificate), and creates the admin account.
- Start menu shortcuts: "Open stats site", "Open admin", "View logs".
- **Upgrade check for `custom/` overrides** `[PROPOSED]` (2026-10-04, TD-25): on an upgrade (not a fresh install) the installer runs `il2ks custom list
  --problems --fail-on-problems` (exit code 4 = an override needs attention; `custom list --json` is the machine-readable form). Interactive installs show
  a message box naming the affected files and the `il2ks custom diff` / `custom accept` workflow, with a pointer to `docs/customizing.md`; silent installs
  write the report to the installer log and to `<data>\logs\installer-custom-check.log`. It never fails the install. Scope and wording: OQ-93.
- **As built and CI-verified (2026-10-04)** `[PROPOSED]`: `packaging/windows/il2ks.iss` (Inno Setup), built by `packaging/windows/build.py` from
  pinned, SHA-256-checked inputs (a bundled relocatable CPython with il2ks installed from `uv.lock`, Caddy, WinSW; `packaging/windows/README.md`).
  Program files in `%ProgramFiles%\il2ks`, everything that is the admin's in `%ProgramData%\il2ks`; the service runs as the virtual account
  `NT SERVICE\il2ks` (OQ-41). Because that account can't read the game's log folder the way SYSTEM could, the installer grants it **Modify** on the
  log folder chosen in the wizard (read access, plus `after_archive = move`); this changes ACLs outside il2ks's own folders, and network folders
  are fine too `[DECIDED]` (maintainer, 2026-10-04, OQ-68). The workflow `windows-installer.yml` (tags `v*` or by hand) builds the installer and runs, on a clean
  `windows-latest` runner: a **silent install** (`/VERYSILENT /NOSERVICE /NOFIREWALL /ADMINPASSWORDFILE=...`, which must delete the password
  file), `il2ks --version` and **`doctor`** from the installed copy (errors fail the job; exit 1 for warnings is fine), the **site answers**
  (`il2ks web --dev`), the **upgrade check** (an outdated override reported in the log and in `installer-custom-check.log`, exit code 0 for the
  install), and an uninstall that **keeps the data**. A second job, `service-test`, installs the **real service** in own-proxy mode
  (`/HTTPS=external`, so no port 80 or 443 is needed), checks it runs as `NT SERVICE\il2ks` and answers, stops it the way Windows does (a
  clean `stopped` in the log, no stray python or caddy), and checks the uninstall removes the service; it is informational
  (`continue-on-error`) until it has passed a few more times. A tag then attaches the installer and its `SHA256SUMS.txt` to the release.
  Silent switches: `/LOGDIR= /TIMEZONE= /DOMAIN= /EMAIL= /HTTPS=caddy|external /ADMINUSER= /ADMINPASSWORDFILE= /NOSETUP /NOSERVICE /NOFIREWALL
  /MERGETASKS="!firewall" /DELETEDATA`; `/ADMINPASSWORD=` is **kept** for silent installs, next to `/ADMINUSER=` and `/ADMINPASSWORDFILE=` (the recommended switch, deleted after use), but shows the password in process lists and the log `[DECIDED]` (maintainer, 2026-10-04, OQ-69).
  **Silent mode skips the custom wizard pages and never validates their edits** (the empty admin-password page made the silent wizard wait
  forever after the files were copied; a silent install that waits for input hung the job for 30 minutes). Hang guards: installer children run
  with stdin from NUL and their command lines are logged; CI runs the installer through `.github/scripts/run-installer.ps1` (a 900 s timeout, then
  it prints the setup log, the il2ks logs and the process tree and kills the installer) and every job has a 30-minute limit.
  **Port 80 is busy on GitHub's runners** (something already listens there): `doctor` reports it as an error, which CI accepts and prints;
  any other doctor error fails the job.
- ✅ The best experience for the actual audience: Next → Next → Finish. No Docker, no terminal, no DB admin.
- ❌ Packaging work: upgrade logic, testing on a clean Windows VM.
- **No code signing** `[DECIDED]` (2026-10-02): the installer ships unsigned. The Windows SmartScreen "unknown publisher" warning is accepted.
  The install docs tell admins to click "More info → Run anyway".
  Without a database server to bundle, it's a lot less work than first estimated.

### C. Manual / developer path
`uv tool install il2ks` (or a `.bat` that installs uv first), then `il2ks setup` (writes config, migrates,
creates the admin, and optionally registers a scheduled task or service), then `il2ks run`. Always SQLite.
- ✅ Nearly free once `il2ks setup` exists. A good fallback, and the dev path.
- ❌ Needs a terminal.
- **This is the Linux path** `[DECIDED]` (2026-10-02). Published to **PyPI** as `il2ks`, installed with `uv tool install il2ks`, `pipx install il2ks`,
  or `pip install il2ks` inside a virtualenv (plain `pip` into the system Python is blocked on recent distros, PEP 668). Linux specifics:
  - **Auto-start:** `il2ks setup` writes (or prints) a **systemd** unit for `il2ks run`, instead of a Windows service.
  - **HTTPS:** Caddy from the distro or official packages, or the admin's own nginx (`https.mode = "external"`, TD-23).
  - **Logs under Wine:** DServer logs live inside the Wine prefix (for example `~/.wine/drive_c/.../data/logs/txt`). `il2ks doctor` checks the path.
  - CI already tests on Ubuntu.

## Recommendation `[PROPOSED]` (updated 2026-10-02)

1. **Write the app to work with any packaging**: one CLI (`il2ks`), one config file plus env vars, no hard-coded paths,
   `setup` and `doctor` commands. A, B and C share the same core.
2. **Iteration 1 ships C** (manual path, SQLite default). It costs almost nothing, and early adopters are tech-savvy server admins.
3. **The Windows installer (B) is the top item in iteration 1.x.** That's where the real UX win is for the main (Windows) audience. **Built and
   CI-verified** (2026-10-04, above).
4. **Docker Compose (A)** for the Linux/Wine minority (SQLite in a volume). It's cheap, but secondary. A separate dev-only compose file provides Postgres for testing (doc 08).

## Networking notes
- **HTTPS only** `[DECIDED]` (TD-23). Public ports are **443** (site) and **80** (redirect to HTTPS + ACME certificate challenge).
  granian listens on `127.0.0.1:8000` only. If 80 or 443 are already taken (for example by IIS), use the "bring your own proxy" mode.
- The installer creates the Windows Firewall rules for 80 and 443 (with an opt-in checkbox).
- Certificates: a domain name pointing at the server (recommended), otherwise an IP-address certificate, otherwise self-signed for testing (TD-23).
- Admins with an existing **nginx or IIS** use `https.mode = "external"`. Sample configs are in the docs (TD-23). The installer offers it as
  `/HTTPS=external` (and as a wizard choice): the service then starts only `web` and `watch` on `127.0.0.1:8000` and Caddy is not run.
- **Port 80** can be taken (IIS, another service, and GitHub's Windows runners): `il2ks doctor` names it, and own-proxy mode avoids the need.
