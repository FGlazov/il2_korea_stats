"""Changes single database fields of the e2e site from a JSON argument (child process, like `branding.py`), and says
what they were: a flow that needs a state the shared world does not have (a mission still running, a sortie that
ended in a ditching) sets it, and the `tweak_data` fixture puts the old values back.

`python -m tests.e2e.tweaks '{"model": "Mission", "pk": 3, "fields": {"is_live": true}}'` prints the previous values
of those fields as JSON. `model` is `Mission`, `PlayerSortie` or `SiteSettings` (no `pk`: the one row). The data
version is bumped (TD-28), so cached pages refresh."""

import json
import sys
from typing import cast


def main() -> None:
    import django

    django.setup()
    from django.db import transaction

    from il2ks.db import models
    from il2ks.db.site import bump_data_version, get_site_settings

    spec = cast("dict[str, object]", json.loads(sys.argv[1]))
    fields = cast("dict[str, object]", spec["fields"])
    model = str(spec["model"])
    assert model in {"Mission", "PlayerSortie", "SiteSettings"}, model
    with transaction.atomic():
        row = get_site_settings() if model == "SiteSettings" else getattr(models, model).objects.get(pk=spec["pk"])
        before = {name: getattr(row, name) for name in fields}
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        bump_data_version()
    print(json.dumps(before))


if __name__ == "__main__":
    main()
