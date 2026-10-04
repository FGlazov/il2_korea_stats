"""The designer brief's file inventory (doc 15) cannot drift from the code and from `static/il2ks/img/`.

The manifest is `il2ks.devtools.assets`; `uv run il2ks dev assets --write` rewrites the brief's generated section."""

from il2ks.devtools import assets


def test_every_referenced_or_shipped_image_is_in_the_manifest() -> None:
    drift = assets.find_drift()
    assert not drift.unlisted_files, f"files in static/il2ks/img/ missing from the manifest: {drift.unlisted_files}"
    assert not drift.unlisted_refs, f"images named in code/templates missing from the manifest: {drift.unlisted_refs}"


def test_no_referenced_or_release_required_image_is_missing() -> None:
    assert not assets.find_drift().missing


def test_release_required_images_are_used_and_have_a_known_source() -> None:
    drift = assets.find_drift()
    assert not drift.unused, (
        f"release-required but referenced nowhere (mark it release=False or wire it): {drift.unused}"
    )
    assert not drift.tabler_unknown, (
        f"neither in img/README.md's Tabler table nor an own drawing: {drift.tabler_unknown}"
    )
    assert not drift.tabler_stale, f"img/README.md lists Tabler files that do not exist: {drift.tabler_stale}"


def test_the_brief_contains_the_current_inventory() -> None:
    text = assets.brief_path().read_text(encoding="utf-8")
    assert assets.brief_section(text) == assets.render(), "run `uv run il2ks dev assets --write` and commit the brief"


def test_every_manifest_file_is_in_the_brief_once() -> None:
    section = assets.render()
    for asset in assets.build_manifest():
        assert section.count(f"| `{asset.path}` |") == 1, asset.path
