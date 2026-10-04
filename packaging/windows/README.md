# Windows installer (doc 07, option B)

Builds `dist/il2ks-setup-<version>.exe`: one unsigned, per-machine installer that bundles everything, so installing needs no
internet (NFR-OFF-1). The admin's guide is [docs/install-windows.md](../../docs/install-windows.md). This file is for whoever builds
and maintains the installer.

## Build

On Windows, with [uv](https://docs.astral.sh/uv/) installed:

```
uv run python packaging/windows/build.py                # -> dist/il2ks-setup-<version>.exe  (a few minutes, ~90 MB download the first time)
uv run python packaging/windows/build.py --stage-only   # only assemble and test the bundle in build/windows/stage
uv run python packaging/windows/build.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"   # use an installed Inno Setup
```

The version is `__version__` in `src/il2ks/__init__.py`. Downloads are cached in `build/windows/downloads`; a file whose SHA-256
differs from `pins.toml` is refused. Nothing is installed on the build machine: the pinned Inno Setup is unpacked (with
`innounp`) into `build/windows/inno-setup`. CI: `.github/workflows/windows-installer.yml` (tags `v*`, or run it by hand).

## The packaging decision: a bundled relocatable CPython, not a frozen executable

The installer ships `python\` (python-build-standalone 3.13, "install_only_stripped") with il2ks and its dependencies installed into
it at build time by `uv pip install --require-hashes` from the exact versions and hashes in `uv.lock`. `il2ks` runs as
`python.exe -m il2ks`. Why not PyInstaller or Nuitka: Django discovers apps, templates, static files, migrations and locale files
by path at run time; granian and Pillow are native extensions; `il2ks run` starts its children as `<this python> -m il2ks`, which
a frozen exe makes awkward; and `il2ks custom copy` shows the admin real template files. A plain Python tree has none of these
problems, the pieces are the same ones developers run (so "works on my machine" is the packaged result), and an upgrade replaces
one folder. Cost: about 140 MB unpacked, 45 to 50 MB compressed.

Checked (Windows 11, 2026-10-03): the built tree was moved to another folder and ran `il2ks --version`, `il2ks doctor`,
`il2ks setup --non-interactive` and `il2ks web --dev`; `il2ks run` started web and watch under the same flags the service uses, and was
stopped the way WinSW stops a service (see below) with a clean exit in under a second.

## Layout of an install

```
%ProgramFiles%\il2ks\          replaced at every upgrade (the installer deletes python\ and bin\ first: no stale packages)
    python\                    CPython + il2ks + dependencies (pre-compiled .pyc)
    bin\caddy.exe              the HTTPS proxy; reaches il2ks through PATH (service XML, il2ks.cmd), not through config
    service\il2ks-service.exe  WinSW; il2ks-service.xml is rendered at build time (winsw.py)
    il2ks.cmd                  `il2ks ...` on the command line (sets PATH and IL2KS_CONFIG)
    il2ks-admin.cmd            elevates, then runs il2ks.cmd (Start menu: doctor, command prompt)
    licenses\, BUILD-INFO.txt
%ProgramData%\il2ks\           the admin's: il2ks.toml, database, secret key, logs\, backups\, custom\ (kept on uninstall)
```

The service (`il2ks`) runs `python.exe -m il2ks --config %ProgramData%\il2ks\il2ks.toml run` as the virtual account `NT SERVICE\il2ks` (set with `sc config ... obj=` after WinSW registers it; Modify on the data folder and the chosen log folder via `icacls`), `python -P` (safe path), delayed automatic start,
priority below normal, restart after a crash (5 s, 10 s, 30 s, then every 60 s), `stoptimeout` 30 s.

**How the service stops** (`il2ks.serving.procutil`, `supervisor`): WinSW attaches to the console of `il2ks run` and sends Ctrl+C.
`il2ks run` stops its children (it sends CTRL_BREAK to each, they share the console) and exits; after `stoptimeout` WinSW kills the
process tree. Verified on a Windows 11 machine by reproducing WinSW's exact call sequence (`AttachConsole`,
`GenerateConsoleCtrlEvent(CTRL_C_EVENT)`) against `il2ks run` started without a window: web (granian and its worker) and watch
stopped cleanly in 0.9 s, no process was left, `run.json` was removed. Not verified against a real Windows service on a clean machine:
the CI job `service-test` does that.

## Inputs (`pins.toml`) and how to bump them

| Input | Version now | Where to get the hash | Notes |
|---|---|---|---|
| python-build-standalone CPython | 3.13.16 (release 20261003) | `SHA256SUMS` in the release <https://github.com/astral-sh/python-build-standalone/releases> | file `cpython-<ver>+<release>-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`; keep it >= `requires-python` |
| Caddy | 2.11.7 | upstream gives SHA-512 (`caddy_<ver>_checksums.txt`); check that, then `Get-FileHash -Algorithm SHA256` | `caddy_<ver>_windows_amd64.zip`; read Caddy's release notes for ACME changes |
| WinSW | 2.12.0 (x64, needs .NET Framework 4.6.1+) | none published: `Get-FileHash` the download | stay on a 2.x release; 3.x is alpha |
| Inno Setup (build tool) | 6.7.3 | `Get-FileHash` | the script avoids 7.x-only features |
| innounp (build tool) | 2.2.11 | `Get-FileHash` | only unpacks Inno Setup for the build |

To bump one: edit `version`, `url` and `sha256` together in `pins.toml`, run `uv run pytest tests/unit/test_windows_packaging.py` (it checks
that the version appears in the URL and the Python still satisfies `requires-python`), then build and run the smoke test (CI does).
Python dependencies are not pinned here: they come from `uv.lock` (`uv lock --upgrade-package ...` as for any release).

## Files

| File | Purpose |
|---|---|
| `build.py` | download and verify inputs, build the wheel, assemble `build/windows/stage`, test it, run ISCC |
| `pins.toml` | the pinned inputs |
| `il2ks.iss` | the Inno Setup script (wizard pages, upgrade, service, firewall, uninstall) |
| `winsw.py` | renders the WinSW service XML |
| `zones.py` | Windows time zone name -> IANA name table, rendered into the wizard |
| `files/il2ks.cmd`, `files/il2ks-admin.cmd` | command-line wrapper, self-elevating wrapper (converted to CRLF by the build) |

Tests: `tests/unit/test_windows_packaging.py`.

## Follow-up: the first-run web setup page

The wizard asks everything itself and runs `il2ks setup --non-interactive`, so the web setup page
(`http://localhost:<port>/setup/?token=<data dir>\setup-token.txt`, created by `il2ks web`/`run` when no admin exists) is not needed.
Once it is merged, the wizard could shrink to "install, start the service", with a finish-page checkbox "Open setup in my browser"
that waits for `%ProgramData%\il2ks\setup-token.txt` and opens the URL (under the service, `il2ks run` restarts its children when
the page writes the config). The `/ADMINPASSWORD`-less silent install (`--no-admin`) would then use that page instead of
`il2ks createadmin`.
