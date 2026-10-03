"""Doctor checks for serving the site (FR-OPS-1): secret key, domain, Caddy, ports, static files, `custom/` overrides.

Each check returns findings in plain words with a `fix` the admin can follow (see `il2ks.ops.doctor`).
"""

import socket
from collections.abc import Callable, Iterable
from pathlib import Path

from il2ks.config import Config
from il2ks.ops.doctor import Finding, Level, check
from il2ks.serving import caddy, custom, procutil
from il2ks.serving.secret import DEV_SECRET_KEY, read_stored_secret_key

MIN_SECRET_KEY_LENGTH = 32
LOCAL_NAME_SUFFIXES = (".local", ".lan", ".localhost", ".internal", ".home", ".localdomain")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def port_in_use(port: int) -> bool:
    """Whether something accepts connections on this port on this machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


port_probe: Callable[[int], bool] = port_in_use
"""Replaced in tests; the real probe above connects to 127.0.0.1."""


@check
def secret_key(cfg: Config) -> Iterable[Finding]:
    title = "Secret key"
    if cfg.web.secret_key == DEV_SECRET_KEY:
        yield Finding(
            Level.ERROR,
            "The secret key is the built-in development key",
            "[web] secret_key is the public placeholder, so anybody could forge login cookies.",
            "Remove secret_key from il2ks.toml (a private one is then generated), or set a long random value.",
        )
    elif cfg.web.secret_key:
        if len(cfg.web.secret_key) < MIN_SECRET_KEY_LENGTH:
            yield Finding(
                Level.WARN,
                "The configured secret key is short",
                f"[web] secret_key has {len(cfg.web.secret_key)} characters.",
                f"Use at least {MIN_SECRET_KEY_LENGTH} random characters, or remove it to have one generated.",
            )
        else:
            yield Finding(Level.OK, title, "set in the configuration")
    elif read_stored_secret_key(cfg.data_dir):
        yield Finding(Level.OK, title, f"generated, kept in {cfg.data_dir / 'secret_key.txt'}")
    else:
        yield Finding(Level.OK, title, "not created yet: `il2ks web` / `il2ks run` generate it on first start")


@check
def debug_mode(cfg: Config) -> Iterable[Finding]:
    if cfg.debug:
        yield Finding(
            Level.WARN,
            "Debug mode is on",
            "`debug = true` (or IL2KS_DEBUG): plain http, error pages show internals, no HTTPS protections.",
            "Turn it off (remove `debug` from il2ks.toml) for any site other people can reach.",
        )


@check
def domain(cfg: Config) -> Iterable[Finding]:
    https = cfg.https
    title = "Domain"
    if not https.domain:
        if https.mode == "caddy":
            yield Finding(
                Level.WARN,
                "No domain is set: HTTPS uses a self-signed test certificate",
                "Browsers will warn that the site is not secure. Fine for testing only.",
                "Set [https] domain in il2ks.toml to your site's name (or public IP); it must point at this machine.",
            )
        elif not cfg.web.allowed_hosts:
            yield Finding(
                Level.WARN,
                "No domain is set: the web server accepts any host name",
                "With your own proxy this is harmless but less strict than it could be.",
                "Set [https] domain (or [web] allowed_hosts) to the name your proxy serves.",
            )
        return
    if caddy.is_ip_address(https.domain):
        yield Finding(
            Level.OK,
            title,
            f"{https.domain} is an IP address: Let's Encrypt certificates for IPs last about 6 days and renew by "
            "themselves; the address must be public, and port 80 must be reachable. Needs Caddy 2.10 or newer.",
        )
    elif (
        https.mode == "caddy"
        and https.cert == "auto"
        and ("." not in https.domain or https.domain.endswith(LOCAL_NAME_SUFFIXES))
    ):
        yield Finding(
            Level.WARN,
            f"'{https.domain}' looks like a local name",
            "Public certificate authorities only certify public domain names.",
            'Use a public name (free dynamic-DNS names work), or set [https] cert = "internal" for testing.',
        )
    else:
        yield Finding(Level.OK, title, https.domain)


@check
def caddy_binary(cfg: Config) -> Iterable[Finding]:
    if cfg.https.mode != "caddy" or cfg.debug:
        return
    found = caddy.find_caddy(cfg)
    if found is not None:
        yield Finding(Level.OK, "Caddy (HTTPS proxy)", str(found))
        return
    configured = cfg.https.caddy_path
    if configured is not None:
        yield Finding(
            Level.ERROR,
            "Caddy was not found at the configured path",
            f"[https] caddy_path = {configured}",
            "Fix the path, or remove caddy_path so Caddy is looked up on the PATH.",
        )
        return
    yield Finding(
        Level.ERROR,
        "Caddy (HTTPS proxy) is not installed",
        f"Not on the PATH and not in {cfg.data_dir / 'bin'}.",
        "Install it: Windows `winget install CaddyServer.Caddy`, Linux see caddyserver.com/docs/install "
        '(docs/install.md). Or run your own proxy and set [https] mode = "external".',
    )


def _ports_to_check(cfg: Config) -> list[tuple[int, str]]:
    ports = [(cfg.web.port, "the web server")]
    if cfg.https.mode == "caddy" and not cfg.debug:
        ports += [(cfg.https.http_port, "Caddy (http)"), (cfg.https.https_port, "Caddy (https)")]
    return ports


@check
def ports(cfg: Config) -> Iterable[Finding]:
    stack = procutil.running_stack(cfg.data_dir)
    for port, role in _ports_to_check(cfg):
        title = f"Port {port} for {role}"
        if not port_probe(port):
            yield Finding(Level.OK, title, "free")
        elif stack is not None:
            yield Finding(Level.OK, title, f"in use by `il2ks run` (PID {stack.pid})")
        else:
            yield Finding(
                Level.ERROR,
                f"Port {port} ({role}) is already in use by another program",
                "Something else is listening there (another il2ks, IIS, nginx, Skype, ...).",
                _port_fix(cfg, port),
            )


def _port_fix(cfg: Config, port: int) -> str:
    if port == cfg.web.port:
        return (
            f"Stop the other program, or choose another [web] port (then point your proxy at it). Find it: {_who(port)}"
        )
    return (
        f'Stop the other program ({_who(port)}), or use your own proxy: [https] mode = "external", '
        "or choose other ports with [https] http_port / https_port (a normal certificate needs 80 and 443)."
    )


def _who(port: int) -> str:
    return f"`netstat -ano | findstr :{port}` on Windows, `ss -ltnp 'sport = :{port}'` on Linux"


@check
def static_files(cfg: Config) -> Iterable[Finding]:
    if cfg.debug:
        return
    manifest = cfg.data_dir / "staticfiles" / "staticfiles.json"
    if manifest.is_file():
        yield Finding(Level.OK, "Static files collected", str(manifest.parent))
    else:
        yield Finding(
            Level.WARN,
            "Static files have not been collected yet",
            f"{manifest} does not exist, so pages would have no styling.",
            "Start `il2ks web` or `il2ks run`: it collects them at every start.",
        )


@check
def external_proxy(cfg: Config) -> Iterable[Finding]:
    if cfg.https.mode != "external":
        return
    yield Finding(
        Level.OK,
        "Your own HTTPS proxy",
        f"forward to http://{cfg.web.host}:{cfg.web.port} and send 'X-Forwarded-Proto: https' and the original Host "
        "header (samples: docs/reverse-proxy.md)",
    )
    if cfg.web.host not in LOOPBACK_HOSTS:
        yield Finding(
            Level.WARN,
            f"The web server listens on {cfg.web.host}, not only on this machine",
            "Anyone who can reach that port can bypass your proxy's HTTPS.",
            'Set [web] host = "127.0.0.1", unless your proxy runs on another machine and a firewall protects the port.',
        )


@check
def custom_overrides(cfg: Config) -> Iterable[Finding]:
    """TD-25: an override whose original changed (an upgrade) may be out of date; one with no original is dead."""
    try:
        statuses = custom.override_statuses(cfg)
        untracked = custom.untracked_overrides(cfg)
    except custom.CustomError as exc:
        yield Finding(Level.WARN, "The custom/ override list is damaged", str(exc), "Delete the file named above.")
        return
    problems = 0
    for status in statuses:
        if status.state == "original-changed":
            problems += 1
            yield Finding(
                Level.WARN,
                f"Override {status.key} may be out of date",
                f"The built-in file changed since you copied it (with il2ks {status.copied_version}). "
                f"Yours: {status.override}  Built-in: {status.original}",
                f"Compare the two, merge what you want, then run `il2ks custom accept {status.key}`.",
            )
        elif status.state == "original-missing":
            problems += 1
            yield Finding(
                Level.WARN,
                f"Override {status.key} has no built-in original any more",
                f"{status.override} replaces a file that il2ks no longer ships, so it is probably unused.",
                "Delete the override file, or keep it if it is deliberate (a page of your own).",
            )
    for kind, rel, original in untracked:
        problems += 1
        yield Finding(
            Level.WARN,
            f"Override {kind}/{rel} was not recorded",
            f"It replaces {original}, but il2ks can't tell whether that file changes in an upgrade.",
            f"Run `il2ks custom copy --force {kind}/{rel}` to record the original (this overwrites your file with the "
            "built-in one, so save your version first), or ignore this.",
        )
    active = [s for s in statuses if s.state != "override-deleted"]
    if active or untracked:
        if not problems:
            yield Finding(Level.OK, "custom/ overrides", f"{len(active)} recorded, all match their originals")
    elif _custom_has_files(cfg.data_dir / "custom"):
        yield Finding(Level.OK, "custom/ overrides", "only new files, nothing replaces a built-in file")


def _custom_has_files(root: Path) -> bool:
    return root.is_dir() and any(p.is_file() and p.name != custom.OVERRIDES_FILE for p in root.rglob("*"))
