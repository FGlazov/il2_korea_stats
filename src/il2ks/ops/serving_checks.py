"""Doctor checks for serving the site (FR-OPS-1): secret key, domain, Caddy, ports, static files, `custom/` overrides.

Each check returns findings in plain words with a `fix` the admin can follow (see `il2ks.ops.doctor`).
"""

import socket
from collections.abc import Callable, Iterable

from il2ks.config import Config
from il2ks.ops.doctor import Finding, Level, check
from il2ks.serving import caddy, custom, procutil
from il2ks.serving.secret import DEV_SECRET_KEY, read_stored_secret_key
from il2ks.web.svg_symbol import NOT_ICONS, SvgError, symbol_markup

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
    """TD-25: an override based on an older version of its built-in file (or on an unknown one) may hide new content or
    break; one whose built-in file is gone is dead."""
    try:
        custom.load_records(cfg)
    except custom.CustomError as exc:
        yield Finding(Level.WARN, "The custom/ override list is damaged", str(exc), "Delete the file named above.")
    checks = custom.override_checks(cfg)
    problems = [c for c in checks if c.is_problem]
    for item in problems:
        yield Finding(Level.WARN, _problem_title(item), f"{item.message} Yours: {item.override}", item.fix)
    if problems:
        return
    overrides = [c for c in checks if c.state != "custom-only"]
    behind = [c for c in overrides if c.state == "behind"]
    if overrides:
        note = f"; {len(behind)} behind by small changes you need not follow (`il2ks custom list`)" if behind else ""
        yield Finding(
            Level.OK, "custom/ overrides", f"{len(overrides)} replacing a built-in file, none out of date{note}"
        )
    elif checks:
        yield Finding(Level.OK, "custom/ overrides", "only new files, nothing replaces a built-in file")


@check
def custom_icons(cfg: Config) -> Iterable[Finding]:
    """A custom SVG under custom/static/il2ks/img/ that cannot become a sprite symbol is left out of the icon sprite
    (the built-in or generic icon, or nothing, shows instead). Same parser as the sprite (`web.svg_symbol`)."""
    root = custom.custom_dir(cfg) / "static" / "il2ks" / "img"
    bad: list[Finding] = []
    count = 0
    for path in sorted(root.rglob("*.svg")) if root.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        if rel.startswith(NOT_ICONS):
            continue
        count += 1
        try:
            symbol_markup(rel.removesuffix(".svg").replace("/", "."), path.read_bytes())
        except (SvgError, OSError) as exc:
            bad.append(
                Finding(
                    Level.WARN,
                    f"The custom icon {rel} is not used",
                    f"{exc}. It is left out of the icon sprite. File: {path}",
                    "Save it as plain SVG (docs/customizing.md, 'Your own SVG icons') or delete it.",
                )
            )
    yield from bad
    if count and not bad:
        yield Finding(Level.OK, "custom/ icons", f"{count} custom SVG file(s), all usable in the icon sprite")


def _problem_title(item: custom.OverrideCheck) -> str:
    if item.state == "orphan":
        return f"Override {item.key} has no built-in original any more"
    if item.state == "unversioned":
        return f"Override {item.key} has no version line: it may be out of date"
    if item.state == "newer":
        return f"Override {item.key} is based on a newer version than this il2ks has"
    return f"Override {item.key} is OUT OF DATE: the built-in file changed in an upgrade"
