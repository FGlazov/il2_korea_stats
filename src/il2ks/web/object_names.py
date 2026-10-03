"""Game object names in the viewer's language (TD-24, FR-ADM-5).

The fallback chain, first hit wins:

1. the admin's override (`GameObject.name_overridden`, the text in `display_name`): deliberate, so it shows in every
   language (an admin who wants translations back resets the name, which clears the override),
2. the shipped translation for the viewer's language (`core/catalog/data/object_names_<language>.csv`),
3. the shipped English default (`GameObject.display_name` while not overridden, i.e. `objects.csv`),
4. the raw log name.

Page views never call this; templates use `{{ obj|object_name }}`. Pure display work, no queries.
"""

from functools import cache

from il2ks.core.catalog.loader import Catalog, load_default_catalog
from il2ks.db.models import GameObject


@cache
def default_catalog() -> Catalog:
    """The shipped catalog, read once per process (it is read-only reference data)."""
    return load_default_catalog()


def name_of(game_object: object, language: str, catalog: Catalog | None = None) -> str:
    """The display name of a `GameObject` for `language` (`de`, `pt-br`, ...). Anything else is shown as `str()`."""
    if not isinstance(game_object, GameObject):
        return "" if game_object is None else str(game_object)
    if game_object.name_overridden and game_object.display_name:
        return game_object.display_name
    book = catalog if catalog is not None else default_catalog()
    return book.translated_name(game_object.log_name, language) or game_object.display_name or game_object.log_name
