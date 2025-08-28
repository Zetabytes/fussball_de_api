from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ApiUrl(BaseModel):
    """
    Defines API URLs for a specific team.
    """

    next_games: str
    prev_games: str
    table: str
    all_info: str


class ClubApiUrl(BaseModel):
    """
    Defines API URLs for a specific club.
    """

    teams: str
    info: str
    full_info: str
    next_games: str
    prev_games: str


class ClubSearchResult(BaseModel):
    """
    Represents a single club in the search results.

    :ivar id: The unique ID of the club on fussball.de.
    :ivar name: The name of the club.
    :ivar logo_url: The URL to the club's logo.
    :ivar city: The city of the club.
    :ivar api: URLs for API endpoints related to this club.
    """

    id: str
    name: str
    logo_url: str
    city: str

    @property
    def api(self) -> ClubApiUrl:
        """
        Generates API URLs for the club.
        """
        return ClubApiUrl(
            teams=f"/api/club/{self.id}/teams",
            info=f"/api/club/{self.id}/info",
            full_info=f"/api/club/{self.id}",
            next_games=f"/api/club/{self.id}/next_games",
            prev_games=f"/api/club/{self.id}/prev_games",
        )


class Team(BaseModel):
    """
    Represents a single team of a club.

    :ivar id: The unique ID of the team on fussball.de.
    :ivar name: The name of the team.
    :ivar fussball_de_url: The absolute URL to the team's page on fussball.de.
    :ivar api: URLs for API endpoints related to this team.
    """

    id: str
    name: str
    fussball_de_url: str

    @property
    def api(self) -> ApiUrl:
        """
        Generates API URLs for the team.
        """
        return ApiUrl(
            next_games=f"/api/team/{self.id}/next_games",
            prev_games=f"/api/team/{self.id}/prev_games",
            table=f"/api/team/{self.id}/table",
            all_info=f"/api/team/{self.id}",
        )


class TableEntry(BaseModel):
    """
    Represents a single row in a league table.

    :ivar place: The rank of the team in the table.
    :ivar team: The name of the team.
    :ivar img: The URL to the team's logo.
    :ivar games: The number of games played.
    :ivar won: The number of wins.
    :ivar draw: The number of draws.
    :ivar lost: The number of losses.
    :ivar goal: The goal ratio (e.g., "50:25").
    :ivar goal_difference: The goal difference.
    :ivar points: The total points.
    :ivar is_promotion: Indicates if the team is in a promotion spot.
    :ivar is_relegation: Indicates if the team is in a relegation spot.
    """

    place: int
    team: str
    img: str
    games: int
    won: int
    draw: int
    lost: int
    goal: str
    goal_difference: int
    points: int
    is_promotion: bool = False
    is_relegation: bool = False


class Table(BaseModel):
    """
    Represents a league table.

    :ivar entries: A list of table entries.
    """

    entries: List[TableEntry]


class MatchEvent(BaseModel):
    """
    Represents a single event in a match course.

    :ivar time: Minute of the event (string, e.g., '43’' or '90+1’').
    :ivar type: Type of the event (goal, yellow-card, substitution, etc.).
    :ivar side: 'home' or 'away', indicating which team the event belongs to.
    :ivar description: Optional description such as "Gelbe Karte", "Auswechslung".
    :ivar score: Optional score at that point in match, if displayed.
    """

    time: str
    type: str
    team: str
    description: Optional[str] = None
    score: Optional[str] = None


class MatchTimeline(BaseModel):
    """
    Represents the structured timeline of a game with all events.
    The raw fussball.de JSON structure is preserved.
    """

    durationSections: int
    duration: int
    extraTimeDuration: int
    first_half: Optional[dict] = Field(default=None, alias="first-half")
    second_half: Optional[dict] = Field(default=None, alias="second-half")

    class Config:
        populate_by_name = True


class Game(BaseModel):
    """
    Represents a single game.

    :ivar datetime_utc: The date and time of the game in UTC.
    :ivar competition: The competition or league name.
    :ivar age_group: The age group for the game (e.g., "Herren").
    :ivar home_team: The name of the home team.
    :ivar home_logo: The URL to the home team's logo.
    :ivar away_team: The name of the away team.
    :ivar away_logo: The URL to the away team's logo.
    :ivar status: The status of the game (e.g., "Abgesagt", "Verlegt").
    :ivar home_score: The score of the home team (for past games).
    :ivar away_score: The score of the away team (for past games).
    :ivar location: The location of the game.
    :ivar location_url: The URL to the location on Google Maps.
    """

    datetime_utc: datetime
    competition: str
    age_group: Optional[str] = None
    home_team: str
    home_logo: str
    away_team: str
    away_logo: str
    status: Optional[str] = None
    home_score: Optional[str] = None
    away_score: Optional[str] = None
    location: Optional[str] = None
    location_url: Optional[str] = None
    match_events: Optional[List[MatchEvent]] = None


class ClubInfoResponse(BaseModel):
    """
    Response model for the combined club info endpoint.
    """

    teams: List[Team]
    prev_games: List[Game]
    next_games: List[Game]


class TeamInfoResponse(BaseModel):
    """
    Response model for the combined team info endpoint.
    """

    table: Optional[Table]
    prev_games: List[Game]
    next_games: List[Game]


class TeamWithDetails(Team):
    """
    Represents a team with its detailed information, including table and games.
    This model extends the base Team model with league table and game lists.
    """

    table: Optional[Table] = None
    prev_games: List[Game] = Field(default_factory=list)
    next_games: List[Game] = Field(default_factory=list)


class FullClubInfoResponse(BaseModel):
    """
    Response model for the full club info endpoint, containing all teams with their details.
    """

    club_prev_games: List[Game]
    club_next_games: List[Game]
    teams: List[TeamWithDetails]
