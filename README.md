# il2ks: IL-2 Sturmovik: Korea stats

Self-hosted statistics website for IL-2 Sturmovik: Korea dedicated servers. It parses the server's mission logs into a database
and serves mission, player and sortie stats as a server-rendered website.

**Status: early development.** The design lives in [design_doc/](design_doc/README.md). Nothing is ready for server admins yet.

Inspired by [IL2 stats](https://github.com/vaal-/il2_stats) by =FB=Vaal and =FB=Isay. See [NOTICE](NOTICE).

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync                       # install Python 3.13 + dependencies
uv run pytest                 # tests on SQLite (default)
uv run ruff check && uv run ruff format --check
uv run pyright                # strict type checking
uv run lint-imports           # core must not import Django
uv run il2ks manage migrate   # create the local SQLite database
```

Postgres is only used on the dev side: `docker compose -f docker/compose.dev.yaml up -d`, then `IL2KS_TEST_DB=postgres uv run pytest`.

## License

MIT, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
