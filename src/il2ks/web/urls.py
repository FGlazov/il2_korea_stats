"""Public URLs. Names are a contract between templates (`{% url "web:..." %}`).

Paths are stable so shared links keep working (FR-WEB-13)."""

from django.urls import URLPattern, path

from il2ks.web.views import (
    aircraft,
    boards,
    language,
    leaderboards,
    live,
    missions,
    players,
    setup,
    sorties,
    styleguide,
)

app_name = "web"
urlpatterns: list[URLPattern] = [
    path("", missions.home, name="home"),
    path("missions/", missions.mission_list, name="mission-list"),
    path("missions/<int:pk>/", missions.mission_detail, name="mission-detail"),
    path("leaderboards/", leaderboards.leaderboard, name="leaderboards"),  # the air score board
    path("leaderboards/<slug:board>/", leaderboards.leaderboard, name="leaderboard"),  # ?tour= &aircraft= &sort=
    path("players/", players.player_search, name="player-search"),  # ?q=<name>
    path("players/<int:pk>/", players.player_detail, name="player-detail"),
    path("players/<int:pk>/sorties/", sorties.player_sorties, name="player-sorties"),  # ?aircraft=<GameObject pk>
    path("players/<int:pk>/killboard/", boards.player_killboard, name="player-killboard"),
    path("streaks/", boards.streak_list, name="streak-list"),
    path("sorties/<int:pk>/", sorties.sortie_detail, name="sortie-detail"),
    path("aircraft/", aircraft.aircraft_list, name="aircraft-list"),  # ?sort=: per-type stats (FR-WEB-8)
    path("aircraft/<int:pk>/", aircraft.aircraft_detail, name="aircraft-detail"),  # pk = GameObject pk
    path("language/", language.set_language, name="set-language"),  # ?language=<code>&next=<local url> (TD-24)
    path("live/", live.live_fragment, name="live"),  # HTMX fragment: online now, its own max-age (FR-ING-12)
    path("setup/", setup.setup, name="setup"),  # first run only: local, token-gated, 404 once setup is done
    path("_styleguide/", styleguide.styleguide, name="styleguide"),  # DEBUG only: 404 otherwise
]
