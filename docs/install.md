# Installing il2ks

This guide gets the stats site running on the machine that hosts your IL-2 Korea server (or on another machine that
receives its logs). It works on **Windows** (10, 11, Server) and **Linux** (also when DServer runs under Wine).
Expect 15 to 30 minutes, most of it waiting for DNS and the certificate.

A Windows installer (double-click, no terminal) is planned. Until then, this is the way: a few copy-paste commands.
On Linux you can also run everything in one container: see [Installing with Docker](install-docker.md).

**Where to type commands:** on Windows, open **PowerShell** (Start menu, type "PowerShell"). Some steps say
"as Administrator": right-click PowerShell and choose *Run as administrator*. On Linux, use any terminal.

Contents: [What you need](#what-you-need) · [1. Install uv and il2ks](#1-install-uv-and-il2ks) ·
[2. First-time setup](#2-first-time-setup) · [3. Install Caddy](#3-install-caddy-the-https-part) ·
[4. Domain, ports and firewall](#4-domain-ports-and-firewall) · [5. Start it](#5-start-it) ·
[6. Start at boot](#6-start-automatically-at-boot) · [Upgrading](#upgrading) · [Backups](#backups) ·
[Linux with Wine](#linux-dserver-under-wine) · [Several servers on one machine](#several-servers-on-one-machine) ·
[Troubleshooting](#troubleshooting)

## What you need

- The machine's **admin rights** (to open ports and start at boot).
- **A domain name** that points at the machine, like `stats.example.com`. Free dynamic-DNS names work. It is what
  gives you a normal, trusted HTTPS certificate. (No domain? See [No domain name](#no-domain-name).)
- **Ports 80 and 443** free on that machine and reachable from the internet. The site is HTTPS only: port 80 just
  redirects and proves to the certificate authority that the domain is yours.
  If something else uses them (IIS, nginx, Apache), use [your own proxy instead](reverse-proxy.md).
- DServer's **text mission logs** switched on, and the folder they are written to. `il2ks doctor` checks this.

## 1. Install uv and il2ks

[uv](https://docs.astral.sh/uv/) installs il2ks and the right Python for it, and keeps them apart from anything else
on the machine.

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the terminal and open a new one, then:

```
uv tool install il2ks
il2ks --version
```

If `il2ks` is "not found", run `uv tool update-shell`, then open a new terminal again.

(`pipx install il2ks` works too, as does `pip install il2ks` inside a virtual environment.)

## 2. First-time setup

```
il2ks setup
```

It asks where DServer writes its logs, which domain to use, and creates the configuration file `il2ks.toml`, the
database, and your admin account. The file is commented; open it any time to change a setting. All settings and what
they do are listed in the example config that ships with il2ks (`il2ks.example.toml`).

The settings you are most likely to touch:

```toml
[logs]
dir = "C:/Games/IL-2 Sturmovik Great Battles/data/Missions/..."   # where DServer writes missionReport files

[https]
domain = "stats.example.com"      # your domain
email = "you@example.com"         # optional: the certificate authority warns you here if renewal ever fails
```

Use forward slashes (`/`) in paths, also on Windows. Write the name only: no `https://`, no port.

**Time zone:** DServer names its logs in its own local time. On Windows, set `[server] timezone = "Europe/Berlin"`
(your IANA zone name) unless the machine runs on UTC.

## 3. Install Caddy (the HTTPS part)

[Caddy](https://caddyserver.com) is a small program that gets and renews your HTTPS certificate by itself and sits in
front of the site. `il2ks run` starts it for you, but it has to be installed first. (Using nginx or IIS instead? Skip
this step and read [reverse-proxy.md](reverse-proxy.md).)

**Windows**, any one of these:

```powershell
winget install CaddyServer.Caddy
```
```powershell
scoop install caddy
```
```powershell
choco install caddy
```

Or download `caddy_..._windows_amd64.zip` from <https://caddyserver.com/download> (or the *Releases* page of
<https://github.com/caddyserver/caddy>), unzip it, and put `caddy.exe` into the **`bin` folder inside your il2ks data
folder** (`il2ks.toml` says where the data folder is; create `bin` if it is missing). il2ks finds it there.

**Linux:** use your distribution's package or Caddy's own repository, see <https://caddyserver.com/docs/install>.
For Debian and Ubuntu:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

**Important on Linux:** the package also starts its *own* Caddy service on ports 80 and 443. Turn that one off, because
`il2ks run` starts its own copy:

```bash
sudo systemctl disable --now caddy
```

(Or download the single `caddy` file from <https://caddyserver.com/download>, `chmod +x caddy`, and put it into the
`bin` folder of your data folder, as on Windows.)

Check with `il2ks doctor`: it reports where it found Caddy. If you keep Caddy somewhere unusual, set
`[https] caddy_path = "C:/tools/caddy.exe"` in `il2ks.toml`.

## 4. Domain, ports and firewall

1. **DNS:** at your domain provider, create an **A record** (`stats` for `stats.example.com`) with the machine's
   **public IP address**. It can take minutes to hours to spread. Check: `nslookup stats.example.com`.
2. **Router or hosting firewall:** forward (or allow) **TCP 80 and 443** to the machine. On a rented server this is the
   provider's firewall page.
3. **The machine's own firewall.**

   Windows, PowerShell **as Administrator**:

   ```powershell
   New-NetFirewallRule -DisplayName "il2ks http"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
   New-NetFirewallRule -DisplayName "il2ks https" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
   ```

   Linux (ufw): `sudo ufw allow 80,443/tcp`. (firewalld: `sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload`.)

If Caddy cannot get a certificate, 90% of the time it is one of these three: DNS not pointing here yet, port 80 not
reachable from outside, or something else already using 80/443. `il2ks doctor` finds the third one.

### No domain name

Without `[https] domain`, il2ks still starts, with a **self-signed test certificate**: browsers show a "not secure"
warning. That is for testing only, and il2ks prints a loud warning.

- If the machine has a **public IPv4 address**, you can put the address itself in `domain = "203.0.113.7"`. Let's
  Encrypt issues real certificates for IP addresses (valid about 6 days, renewed automatically), and Caddy 2.10 or
  newer can order them. Port 80 must be reachable. Private addresses (192.168.x.x) cannot get one.
- For a site only your own machine or LAN uses, `[https] cert = "internal"` makes the test certificate explicit.

## 5. Start it

```
il2ks run
```

That one command starts the web server, the log watcher and Caddy, restarts any of them that crashes, and stops them
all cleanly on Ctrl+C. Leave the window open. After 10 to 30 seconds, open `https://stats.example.com`.

- Create (or add) an admin account any time: `il2ks createadmin`. The admin area is at `/admin/`.
- Logs are in the data folder under `logs/` (one file per day per program).
- Try the site on plain http on your own machine, without Caddy or a domain: `il2ks web --dev` and open
  <http://127.0.0.1:8000> (developer mode, **never** for a public site).

Ctrl+C in the window stops everything. A closed terminal window also stops it: use the next step to run it in the
background.

## 6. Start automatically at boot

il2ks prints the right thing for your system, and only changes the machine when you add `--install`.

**Windows** (PowerShell **as Administrator**): creates a task that starts `il2ks run` at boot (30 seconds after, so the
network is up) and restarts it if it dies. It asks for the password of the account to run under.

```powershell
il2ks service schtasks            # shows the command, changes nothing
il2ks service schtasks --install  # does it
schtasks /Run /TN il2ks           # start it now
```

Use `--user SYSTEM` to run it without a stored password. To stop it: `schtasks /End /TN il2ks`. To remove it:
`schtasks /Delete /TN il2ks /F`. (A proper Windows service with the installer is planned.)

**Linux** (systemd):

```bash
il2ks service systemd             # shows the unit, changes nothing
sudo "$(which il2ks)" service systemd --install   # writes /etc/systemd/system/il2ks.service, enables and starts it
journalctl -u il2ks -f            # follow the log
```

(`sudo` may not find a tool installed by uv; `$(which il2ks)` hands it the full path.) The unit lets Caddy use ports 80
and 443 without running as root.

## Upgrading

```
uv tool upgrade il2ks
```

Then restart il2ks (Windows: `schtasks /End /TN il2ks` then `schtasks /Run /TN il2ks`; Linux:
`sudo systemctl restart il2ks`; in a window: Ctrl+C and `il2ks run`). Database migrations run by themselves at start,
after an automatic backup. Your data folder (database, logs, `custom/`) is untouched.

After an upgrade run `il2ks doctor`: it tells you if a template you customized has changed
([customizing.md](customizing.md)).

## Backups

`il2ks backup` writes a dated zip with everything that cannot be rebuilt from the game logs (database, configuration,
`custom/`), and `il2ks restore <zip>` brings it back. Details: `il2ks backup --help`. The original game logs are kept
(archived) in the data folder as well: keep that folder safe.

## Linux, DServer under Wine

DServer's logs live inside the Wine prefix, for example
`~/.wine/drive_c/Program Files/IL-2 Sturmovik Great Battles/data/...` (the exact place depends on your install and
prefix, `WINEPREFIX`). Point `[logs] dir` at that folder as a normal Linux path, like
`/home/you/.wine/drive_c/.../logs`. `il2ks doctor` tells you if the folder is missing or holds no mission reports.
Run il2ks as the same Linux user that owns the prefix, or make sure it can read the folder.

## Several servers on one machine

Each game server gets its own install: its own data folder, its own `il2ks.toml`, its own service name
(`il2ks service ... --name stats-b`) and its own **web port** (`[web] port`). Only one of them can own ports 80 and
443: give the others another proxy ([reverse-proxy.md](reverse-proxy.md)) or other ports (`[https] https_port`, then
the address has to include the port, and certificates need a different challenge, so a shared proxy is easier).
Point each with `il2ks --config path\to\il2ks.toml ...`.

## Troubleshooting

Always start here:

```
il2ks doctor
```

It checks the configuration, ports, Caddy, the secret key, the log folder and your `custom/` files, and says what to do
for each problem.

| What you see | Likely reason and fix |
|---|---|
| Browser says "not secure" / certificate error | No `[https] domain` set (test certificate), or the real certificate is not issued yet. Set the domain; look in the data folder's `logs/run-*.log` for lines from `caddy`. |
| `502 Bad Gateway` | Caddy is up but the web server is not (yet). Wait 30 seconds after start; else read `logs/web-*.log`. |
| `il2ks run: Caddy ... was not found` | Install Caddy ([step 3](#3-install-caddy-the-https-part)) or set `[https] caddy_path`. |
| Doctor: "Port 443 is already in use" | Another program uses it (IIS, nginx, Apache, another Caddy). Stop it, or use [your own proxy](reverse-proxy.md). Find it: `netstat -ano \| findstr :443` (Windows), `sudo ss -ltnp 'sport = :443'` (Linux). |
| Certificate is never issued | DNS does not point here, or port 80 is blocked from outside. Check from your phone (mobile data): `http://stats.example.com` should redirect to https. |
| "another il2ks writer is running" | Normal for a moment while `watch` ingests. If it persists, something else (a `reprocess`) holds the lock; wait. |
| The site works but is empty | `[logs] dir` is wrong or DServer's text logs are off. `il2ks doctor`, then `il2ks ingest` to see what it finds. |
| Page has no styling | Static files were not collected. `il2ks web`/`run` do this at every start; check `logs/web-*.log`. |
| Forgot the admin password | `il2ks createadmin` again with the same name (see `--help`), or `il2ks manage changepassword NAME`. |

Still stuck? Open an issue at <https://github.com/FGlazov/il2_korea_stats/issues> and paste the output of
`il2ks doctor` and the last lines of the newest files in `logs/`. **Do not paste `secret_key.txt` or your config if it
contains a `secret_key`.**
