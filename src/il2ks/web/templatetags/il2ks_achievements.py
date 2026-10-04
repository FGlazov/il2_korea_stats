"""Reads for the medal blocks that other pages embed: `{% load il2ks_achievements %}`.

Simple tags that fetch and hand the rows to the including template (TD-22: plain SELECTs, `il2ks.queries.achievements`):
`player_medals player as medals` (the best tier of each achievement the player holds) and
`sortie_medals sortie as medals` (the tiers first reached in that sortie). One query each.
"""

from django import template

from il2ks.db.models import Player, PlayerSortie
from il2ks.queries import achievements as reads
from il2ks.web import medals

register = template.Library()


@register.simple_tag
def player_medals(player: Player) -> list[medals.Medal]:
    return medals.medals_of(reads.player_rows(player.pk), highest_only=True)


@register.simple_tag
def sortie_medals(sortie: PlayerSortie) -> list[medals.Medal]:
    return medals.medals_of(reads.sortie_rows(sortie), highest_only=False)
