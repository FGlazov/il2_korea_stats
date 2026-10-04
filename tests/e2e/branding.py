"""Sets the e2e site's branding from a JSON argument, the way the admin save would (child process, like `world.py`).

`python -m tests.e2e.branding '{"links": [["Discord", "https://d.example/", "discord"]], "site_title": "X"}'`
Keys: `links` ([label, url, icon] triples), `site_title`, `server_name`, `theme`, `heading_font`, `body_font`. Missing
keys are reset to the default, so `{}` restores the shipped look. The data version is bumped (TD-28).
"""

import json
import sys


def main() -> None:
    import django

    django.setup()
    from il2ks.db.site import bump_data_version, get_site_settings

    spec: dict[str, object] = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    row = get_site_settings()
    triples = spec.get("links", [])
    assert isinstance(triples, list)
    row.links = [{"label": label, "url": url, "icon": icon} for label, url, icon in triples]  # pyright: ignore[reportUnknownVariableType]
    row.site_title = str(spec.get("site_title", "IL-2 Korea stats"))
    row.server_name = str(spec.get("server_name", ""))
    row.theme = spec.get("theme", {})  # pyright: ignore[reportAttributeAccessIssue]
    row.heading_font = str(spec.get("heading_font", ""))
    row.body_font = str(spec.get("body_font", ""))
    row.save()
    bump_data_version()


if __name__ == "__main__":
    main()
