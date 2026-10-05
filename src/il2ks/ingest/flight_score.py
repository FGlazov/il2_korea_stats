"""The admin's flight-time score option, wanted vs applied (maintainer request, 2026-10-05; the pattern of
`ingest.achievements`): `SiteSettings.score_flight` is what the admin chose, `score_flight_applied` what the stored
sortie scores were computed with. Every score computed at ingest uses the applied settings, so incremental == rebuild;
a change is applied by `ingest.score_apply.rescore_with_wanted` (`watch`) or by any `rebuild_aggregates`."""

from dataclasses import replace

from il2ks.core.ratings.score import FlightScore, ScoreRules
from il2ks.db.models import SiteSettings


def wanted_flight_score() -> FlightScore:
    raw = SiteSettings.objects.filter(pk=1).values_list("score_flight", flat=True).first()
    return FlightScore.from_json(raw)


def applied_flight_score() -> FlightScore:
    raw = SiteSettings.objects.filter(pk=1).values_list("score_flight_applied", flat=True).first()
    return FlightScore.from_json(raw)


def flight_score_pending() -> bool:
    return wanted_flight_score() != applied_flight_score()


def adopt_wanted_flight_score() -> FlightScore:
    """Make the wanted settings the applied ones, for a full rebuild that scores every sortie with them anyway."""
    flight = wanted_flight_score()
    SiteSettings.objects.filter(pk=1).update(score_flight_applied=flight.to_json())
    return flight


def with_flight_score(rules: ScoreRules, flight: FlightScore) -> ScoreRules:
    """`rules` with the admin's flight-time option (the config file never sets it)."""
    return replace(rules, flight_time_enabled=flight.enabled, flight_time_per_hour=flight.per_hour)


def with_applied_flight_score(rules: ScoreRules) -> ScoreRules:
    return with_flight_score(rules, applied_flight_score())
