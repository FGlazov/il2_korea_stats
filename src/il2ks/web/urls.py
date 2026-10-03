"""Public URLs. Names are a contract between templates (`{% url "web:..." %}`).

Paths are stable so shared links keep working (FR-WEB-13)."""

from django.urls import URLPattern, path

from il2ks.web.views import missions, players, sorties, styleguide

app_name = "web"
urlpatterns: list[URLPattern] = [
    path("", missions.home, name="home"),
    path("missions/", missions.mission_list, name="mission-list"),
    path("missions/<int:pk>/", missions.mission_detail, name="mission-detail"),
    path("players/", players.player_search, name="player-search"),  # ?q=<name>
    path("players/<int:pk>/", players.player_detail, name="player-detail"),
    path("players/<int:pk>/sorties/", sorties.player_sorties, name="player-sorties"),  # ?aircraft=<GameObject pk>
    path("sorties/<int:pk>/", sorties.sortie_detail, name="sortie-detail"),
    path("_styleguide/", styleguide.styleguide, name="styleguide"),  # DEBUG only: 404 otherwise
]
