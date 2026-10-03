# il2ks: IL-2 Sturmovik: Korea stats

Self-hosted statistics website for IL-2 Sturmovik: Korea dedicated servers. It parses the server's mission logs into a database
and serves mission, player and sortie stats as a server-rendered website.

**Status: early development.** The design lives in [design_doc/](design_doc/README.md). Nothing is ready for server admins yet.

Inspired by [IL2 stats](https://github.com/vaal-/il2_stats) by =FB=Vaal and =FB=Isay. See [NOTICE](NOTICE).

## For server admins

Written for people who run a game server, not for programmers: short steps, copy-paste commands.

- [Installing il2ks](docs/install.md): Windows and Linux, HTTPS, start at boot, upgrading, troubleshooting with `il2ks doctor`.
- [Installing with Docker](docs/install-docker.md): one Compose file for Linux hosts (also DServer under Wine).
- [Using your own proxy (nginx, IIS)](docs/reverse-proxy.md): when ports 80/443 are already taken.
- [Customizing the site](docs/customizing.md): branding in the admin, and replacing templates and files in `custom/`.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync                       # install Python 3.13 + dependencies
uv run pytest                 # tests on SQLite (default)
uv run ruff check && uv run ruff format --check
uv run pyright                # strict type checking
uv run lint-imports           # core must not import Django
uv run il2ks manage migrate   # create the local SQLite database
uv run il2ks web --dev        # the site on plain http://127.0.0.1:8000 (debug mode; never for a public site)
```

`uv run il2ks run` starts everything as an admin would (web + log watcher + Caddy); see [docs/install.md](docs/install.md).
To publish a release: bump `__version__` in `src/il2ks/__init__.py`, tag `v<version>`, push the tag (`.github/workflows/release.yml`).

Postgres is only used on the dev side: `docker compose -f docker/compose.dev.yaml up -d`, then `IL2KS_TEST_DB=postgres uv run pytest`.

## License

MIT, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
