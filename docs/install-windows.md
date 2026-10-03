# Installing il2ks on Windows (the installer)

Double-click, answer a few questions, done: no terminal, no Python, no database to set up, no internet needed while
installing. The installer puts il2ks on the machine, starts it as a **Windows service** (it starts at boot and restarts
itself if it crashes) and adds Start menu entries. Expect about 10 minutes, most of it the web address and certificate.

Prefer the command line, or running on Linux? See [install.md](install.md).

Contents: [What you need](#what-you-need) · [1. Download and start](#1-download-and-start-the-installer) ·
[2. The wizard](#2-the-wizard-step-by-step) · [3. After the install](#3-after-the-install) ·
[Where things are](#where-things-are) · [Upgrading](#upgrading) · [Uninstalling](#uninstalling) ·
[Scripted installs](#scripted-silent-installs) · [Troubleshooting](#troubleshooting)

## What you need

- Windows 10, Windows 11 or Windows Server 2016 or newer (64-bit), and **administrator rights** (the installer asks for them).
- DServer with **text mission logs switched on**, and the folder it writes them to.
  The installer looks for that folder and suggests it; `il2ks doctor` (Start menu) checks it afterwards.
- For a public site: a **domain name** pointing at this machine, and ports **80 and 443** free and reachable from the internet
  ([install.md, step 4](install.md#4-domain-ports-and-firewall) explains DNS and router settings). No domain? It still works,
  with a "not secure" test certificate, which is fine for trying it out.

## 1. Download and start the installer

Download `il2ks-setup-<version>.exe` from the **Releases** page: <https://github.com/FGlazov/il2_korea_stats/releases>.

The installer is **not code-signed** (a certificate costs money every year), so Windows warns you the first time:

1. A blue window, **"Windows protected your PC"**, appears. Click **More info**.
2. Click **Run anyway**.
3. Windows asks "Do you want to allow this app to make changes?": **Yes**.

If your browser says the file "isn't commonly downloaded", choose **Keep**. The release page lists the file's SHA-256 checksum
(`SHA256SUMS.txt`); compare it with `Get-FileHash il2ks-setup-<version>.exe` in PowerShell if you want to be sure.

## 2. The wizard, step by step

| Page | What to do |
|---|---|
| **Welcome, install folder** | Next. The program goes to `C:\Program Files\il2ks`. (Your data does not: see [where things are](#where-things-are).) |
| **Additional tasks** | **Allow visitors through Windows Firewall** opens TCP ports 80 and 443 for il2ks's own web proxy (Caddy) only. Leave it ticked for a public site. Untick it if you use your own proxy or only look at the site on this machine. |
| **Install** | A minute or so. |
| **Game server logs** | The installer searched this machine and filled in the folder with the newest mission reports. Check it, or Browse. Empty is fine: set it later in `il2ks.toml`. |
| **Time zone and web address** | **Time zone**: DServer names its logs in the machine's local time, so il2ks needs the zone as an IANA name (`Europe/Berlin`, `Asia/Seoul`, `America/New_York`, or `UTC`). It is pre-filled when your Windows zone is recognised. **Domain**: optional, like `stats.example.com`; without it the site uses a self-signed test certificate. **E-mail**: optional; the certificate authority writes here only if renewal fails. |
| **HTTPS** | **il2ks does it (bundled Caddy)**: recommended. **My own web server**: for IIS or nginx that already use ports 80/443; il2ks then serves only `127.0.0.1:8000` and you point your proxy at it ([reverse-proxy.md](reverse-proxy.md)). |
| **Admin account** | User name and password (at least 8 characters, not a common password). This is how you log in to `/admin/`. |
| **Finish** | The installer creates the configuration and database, registers and starts the service. "Open the stats site now" opens the browser. |

If a step fails, a message says which, and what to do (usually: run **Run doctor** from the Start menu).

## 3. After the install

The Start menu folder **il2ks** has:

- **Open stats site** and **Open admin**: your site and its admin area (`https://your-domain/`, or `https://localhost/` without a domain).
- **View logs**: the folder with il2ks's log files.
- **Run doctor (check the setup)**: asks for administrator rights, then checks configuration, ports, Caddy, the log folder and the
  database, and says what to fix.
- **il2ks command prompt**: a prompt where the `il2ks` command works (`il2ks backup`, `il2ks createadmin`, ...), run as administrator.
- **Uninstall il2ks**.

The site needs 10 to 30 seconds after a start (and after a reboot the service waits about two minutes: it starts "delayed" so the
game server comes up first). To see the service: Start menu, **Services**, entry **il2ks stats site**. From an administrator
command prompt: `net stop il2ks` and `net start il2ks`.

Now check the DNS, router and firewall points in [install.md, step 4](install.md#4-domain-ports-and-firewall) if the site is not
reachable from outside.

## Where things are

| What | Where |
|---|---|
| Program files (replaced at every upgrade) | `C:\Program Files\il2ks` |
| **Your data**: database, archived mission logs, backups, `custom\` overrides, secret key | `C:\ProgramData\il2ks` |
| Configuration | `C:\ProgramData\il2ks\il2ks.toml` (a commented text file; change settings there, then restart the service) |
| Log files (one per program per day, 14 days kept) | `C:\ProgramData\il2ks\logs` |
| Backups | `C:\ProgramData\il2ks\backups` |
| The Windows service | `il2ks` (display name "il2ks stats site"), runs as the local SYSTEM account, slightly below normal priority |

`C:\ProgramData` is a hidden folder: type the path into the Explorer address bar. It is readable by administrators and the
service only (it holds the database and the secret key). Open `il2ks.toml` with Notepad **run as administrator**.
All settings are explained in the file itself; `docs/customizing.md` covers branding and template overrides.

The installer puts one site on the machine. For several game servers on one machine, use the
[manual install](install.md#several-servers-on-one-machine).

## Upgrading

Download the newer `il2ks-setup-<version>.exe` and run it over the old install. It notices the existing install and
**keeps your configuration and data**: it stops the service, writes a **backup** (`il2ks backup`, into
`C:\ProgramData\il2ks\backups`), replaces the program files, registers the service again and starts it. Database changes are applied
when the service starts, after one more automatic backup. No questions are asked. If the backup fails, the upgrade stops before it
changes anything.

Afterwards run **Run doctor** from the Start menu: it tells you if a template you customized has changed.

## Uninstalling

Settings, Apps, **il2ks stats site**, Uninstall (or **Uninstall il2ks** in the Start menu). It stops and removes the service and
the firewall rules and deletes the program files. **Your data is kept** unless you answer *Yes* to "Also delete your il2ks data":
installing again later picks it up where you left off.

## Scripted (silent) installs

For rollouts or testing, the installer takes its answers from switches. Run it from a command prompt as administrator:

```
il2ks-setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOGDIR="D:\IL-2\logs" /TIMEZONE=Europe/Berlin ^
    /DOMAIN=stats.example.com /EMAIL=you@example.com /ADMINUSER=admin /ADMINPASSWORD="a long password"
```

| Switch | Meaning |
|---|---|
| `/LOGDIR=`, `/TIMEZONE=`, `/DOMAIN=`, `/EMAIL=` | the answers of the pages above |
| `/HTTPS=caddy` or `/HTTPS=external` | bundled Caddy (default) or your own proxy |
| `/ADMINUSER=`, `/ADMINPASSWORD=` | the admin account (without a password, none is created: `il2ks createadmin` later) |
| `/NOSETUP` | copy the files only; run `il2ks setup` yourself |
| `/NOSERVICE` | no Windows service, nothing started (also no firewall rules) |
| `/NOFIREWALL` or `/MERGETASKS="!firewall"` | do not open ports 80 and 443 |
| `/DIR="C:\il2ks"` | program folder (the data folder is always `C:\ProgramData\il2ks`) |
| `/DELETEDATA` | with the uninstaller (`unins000.exe`): also delete the data |
| `/LOG="C:\temp\il2ks-setup.log"` | write the installer's own log |

Running it again over an install (same switches or none) is an upgrade.

## Troubleshooting

Start with **Run doctor** in the Start menu, then the newest files in `C:\ProgramData\il2ks\logs` (the `run-` file tells how
the service started the parts; `web-`, `watch-` and `caddy`-lines show the rest). The installer's own log is
`%TEMP%\Setup Log <date>.txt`.

| What you see | Likely reason and fix |
|---|---|
| Windows blocks the installer ("Windows protected your PC") | Normal for an unsigned installer: **More info**, **Run anyway**. |
| The service is "Stopped" right after the install | Run doctor. Usually a port is taken (IIS or another web server on 80/443: choose *own web server* mode, see [reverse-proxy.md](reverse-proxy.md)) or the configuration has an error. After fixing: `net start il2ks`. |
| Site not reachable from outside | DNS not pointing here, router or provider firewall closing 80/443, or the firewall task was unticked. See [install.md, step 4](install.md#4-domain-ports-and-firewall). |
| Browser: "not secure" | No domain was entered (test certificate), or the real certificate is not issued yet (look for `caddy` lines in `logs\run-*.log`). Add `[https] domain` to `il2ks.toml`, then `net stop il2ks` and `net start il2ks`. |
| Site is empty | The log folder is wrong or DServer's text logs are off. Doctor says which. The service must be able to read (and, with the default `after_archive = "move"`, change) that folder: it runs as SYSTEM, which cannot reach network drives that need your login. |
| Forgot the admin password | In the **il2ks command prompt**: `il2ks createadmin --username NAME` (asks for a new password). |
| Admin account was not created during the install | The password was refused (too common?). Create it in the **il2ks command prompt**: `il2ks createadmin`. |
| Antivirus quarantines a file | The installer is unsigned and bundles Python, Caddy and a service wrapper, which some scanners dislike. Allow-list `C:\Program Files\il2ks` and report the false positive. |

Still stuck? Open an issue at <https://github.com/FGlazov/il2_korea_stats/issues> with the output of **Run doctor** and the last
lines of the newest log files. **Do not paste `secret_key.txt`.**
