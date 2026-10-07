"""Sets the e2e site's branding from a JSON argument, the way the admin save would (child process, like `world.py`).

`python -m tests.e2e.branding '{"links": [["Discord", "https://d.example/", "discord"]], "site_title": "X"}'`
Keys: `links` ([label, url, icon] triples), `site_title`, `server_name`, `theme`, `heading_font`, `body_font`,
`font_files` ({"heading": path, "body": path} of .woff2 files to upload as custom fonts and select),
`feature_image` ({"path", "alt", "caption"}: the large front-page image, produced right away),
`notice` ({"text", "level", "until": an ISO time or nothing}: the site-wide notice banner), `home_bg` / `header_bg`
({"file", "position", "shade"}: a background picture, processed like an admin upload). Missing keys are
reset to the default, so `{}` restores the shipped look. The data version is bumped (TD-28).
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import cast


def main() -> None:
    import django

    django.setup()
    from django.conf import settings

    from il2ks.db.site import bump_data_version, get_site_settings
    from il2ks.web.fonts import process_font
    from il2ks.web.logo import store_bytes

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
    row.custom_fonts = []
    uploads = cast("dict[str, str]", spec.get("font_files", {}))
    for role, source in uploads.items():
        font = process_font(Path(str(source)).read_bytes(), Path(str(source)).name)
        store_bytes(font.data, font.font.file, Path(settings.MEDIA_ROOT))
        row.custom_fonts.append(font.font.as_json())
        setattr(row, f"{role}_font", font.font.key)
    from il2ks.web.branding_images import process_background

    for kind in ("home", "header"):
        spec_bg = cast("dict[str, object] | None", spec.get(f"{kind}_bg"))
        picture = process_background(Path(str(spec_bg["file"])).read_bytes(), kind) if spec_bg else None
        if picture is not None and spec_bg is not None:
            store_bytes(picture.data, picture.name, Path(settings.MEDIA_ROOT))
            setattr(row, f"{kind}_bg_position", str(spec_bg.get("position", "center")))
            setattr(row, f"{kind}_bg_shade", int(str(spec_bg.get("shade", 70))))
        setattr(row, f"{kind}_bg_image", picture.name if picture else "")
    feature = cast("dict[str, str] | None", spec.get("feature_image"))
    row.home_feature = "image" if feature else "none"
    row.feature_image_path = feature["path"] if feature else ""
    row.feature_alt = feature.get("alt", "") if feature else ""
    row.feature_caption = feature.get("caption", "") if feature else ""
    row.feature_source_sig = ""
    row.feature_image = row.feature_image_small = row.feature_error = ""
    notice = cast("dict[str, str] | None", spec.get("notice"))
    row.notice_text = notice["text"] if notice else ""
    row.notice_level = notice.get("level", "info") if notice else "info"
    row.notice_until = datetime.fromisoformat(notice["until"]) if notice and notice.get("until") else None
    row.save()
    if feature:
        from il2ks.web.feature_image import sync

        sync(force=True)
    bump_data_version()


if __name__ == "__main__":
    main()
