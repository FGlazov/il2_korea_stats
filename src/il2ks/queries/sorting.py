"""ORDER BY helpers for the sortable list pages (TD-22: plain expressions, no aggregation).

A page's sort whitelist maps a public `?sort=` key to a `SortSpec`: a column name, a `Ratio` of two columns, a `Rated`
column or a `Computed` expression. Ratios and ratings have no value where their denominator or game count is 0; those
rows get NULL and always sort last, ascending or descending, on SQLite and Postgres alike (their default NULL placement
differs).
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import Case, Expression, ExpressionWrapper, F, FloatField, OrderBy, Value, When
from django.db.models.functions import Greatest


@dataclass(frozen=True, slots=True)
class Ratio:
    """`numerator * scale / denominator`, undefined (NULL) while the denominator is 0. `minus` makes the numerator
    `numerator - minus` clamped at 0 (survival: sorties minus deaths)."""

    numerator: str
    denominator: str
    scale: float = 1.0
    minus: str = ""


@dataclass(frozen=True, slots=True)
class Rated:
    """A column that only means something when `games` is above 0 (an Elo: 1500 is just the starting value)."""

    value: str
    games: str


@dataclass(frozen=True, slots=True)
class Computed:
    """Any expression of the row: `build(prefix)` returns it (`prefix` reaches the columns through a relation). A NULL
    result sorts last in both directions (a hidden player's name, a gunner's missing combat role)."""

    build: Callable[[str], Expression]


type SortSpec = str | Ratio | Rated | Computed


def order_by(spec: SortSpec, sort: str, prefix: str = "") -> Expression | OrderBy:
    """The ORDER BY term for a resolved `sort` ('kd' or '-kd'); `prefix` reaches the columns through a relation
    ('player__'). Undefined values come last in both directions."""
    descending = sort.startswith("-")
    match spec:
        case str():
            plain = F(prefix + spec)
            return plain.desc() if descending else plain.asc()
        case Ratio():
            top = (
                Greatest(F(prefix + spec.numerator) - F(prefix + spec.minus), Value(0))
                if spec.minus
                else F(prefix + spec.numerator)
            )
            expression = Case(
                When(
                    **{f"{prefix}{spec.denominator}__gt": 0},
                    then=ExpressionWrapper(top * spec.scale / F(prefix + spec.denominator), output_field=FloatField()),
                ),
                default=None,
                output_field=FloatField(),
            )
        case Rated():
            expression = Case(
                When(**{f"{prefix}{spec.games}__gt": 0}, then=F(prefix + spec.value)),
                default=None,
                output_field=FloatField(),
            )
        case Computed():
            expression = spec.build(prefix)
    return expression.desc(nulls_last=True) if descending else expression.asc(nulls_last=True)
