import asyncio
import logging
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .cache import load_caches_from_file, save_caches_to_file
from .config import settings
from .crawler import (
    get_club_next_games,
    get_club_prev_games,
    get_club_teams,
    get_team_next_games,
    get_team_prev_games,
    get_team_table,
    search_clubs,
    get_game_by_id,
)
from .logging_config import setup_logging
from .schemas import (
    ClubInfoResponse,
    ClubSearchResult,
    FullClubInfoResponse,
    Game,
    Table,
    Team,
    TeamInfoResponse,
    TeamWithDetails,
)
from .security import get_api_key

setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fussball.de API",
    description="A lightweight, self-hosted Python API to crawl and provide data from fussball.de.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/examples", StaticFiles(directory="../examples"), name="examples")


async def prewarm_cache():
    """
    A background task that periodically fetches data for a configured club
    to keep the cache warm. Limits concurrent requests with a semaphore.
    """
    logger.info(
        f"Starting cache pre-warming for club ID: {settings.PREWARM_CLUB_ID} "
        f"with an interval of {settings.PREWARM_INTERVAL_SECONDS} seconds."
    )
    sem = asyncio.Semaphore(5)

    async def run_with_limit(coro):
        async with sem:
            return await coro

    while True:
        try:
            club_id = settings.PREWARM_CLUB_ID
            logger.info(f"Running pre-warming cycle for club: {club_id}")

            # First, fetch the list of teams for the club.
            teams = await get_club_teams(club_id)
            tasks = []
            if not teams:
                logger.warning(f"Pre-warming: Could not fetch teams for club {club_id}.")
            else:
                # Prepare all data fetching tasks.
                tasks = [
                    run_with_limit(get_club_next_games(club_id)),
                    run_with_limit(get_club_prev_games(club_id)),
                ]
                for team in teams:
                    tasks.append(run_with_limit(get_team_next_games(team.id)))
                    tasks.append(run_with_limit(get_team_prev_games(team.id)))
                    tasks.append(run_with_limit(get_team_table(team.id)))

                # Execute all tasks concurrently, ignoring individual errors.
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Pre-warming task failed with exception: {result}")

            # Build and cache full club info object
            from .schemas import FullClubInfoResponse, TeamWithDetails
            from .cache import club_info_cache
            try:
                if teams:
                    teams_with_details = []
                    for i, team in enumerate(teams):
                        next_games = results[2 + i*3]
                        prev_games = results[2 + i*3 + 1]
                        table = results[2 + i*3 + 2]
                        teams_with_details.append(
                            TeamWithDetails(
                                **team.model_dump(),
                                table=table,
                                next_games=next_games,
                                prev_games=prev_games,
                            )
                        )
                    club_info_cache[club_id] = FullClubInfoResponse(
                        club_prev_games=results[1],
                        club_next_games=results[0],
                        teams=teams_with_details,
                    )
                    logger.debug(f"Updated club_info_cache for {club_id} with {len(teams_with_details)} teams")
            except Exception as e:
                logger.error(f"Failed to build full club info object during pre-warming: {e}")

            logger.info(f"Pre-warming cycle for club {club_id} completed.")

        except Exception as e:
            logger.critical(f"An unexpected error occurred in the pre-warming task: {e}")

        # Wait for the configured interval before the next run.
        await asyncio.sleep(settings.PREWARM_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup_event():
    """
    Actions to perform on application startup.
    - Starts the cache pre-warming background task if configured.
    """
    # Load caches if available
    load_caches_from_file()
    logger.info("Persistent cache loaded from file (if it existed).")

    if settings.PREWARM_CLUB_ID:
        asyncio.create_task(prewarm_cache())
    else:
        logger.info("Cache pre-warming is disabled. Set PREWARM_CLUB_ID to enable it.")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Actions to perform on application shutdown.
    """
    save_caches_to_file()
    logger.info("Persistent cache saved to file.")


@app.get("/")
async def read_root():
    """
    Root endpoint of the API.
    """
    logger.debug("Root endpoint requested.")
    return {"message": "Welcome to the Fussball.de API. See /docs for documentation."}


@app.get(
    "/api/search/clubs",
    response_model=List[ClubSearchResult],
    dependencies=[Depends(get_api_key)],
)
async def search_for_clubs(query: str = Query(..., min_length=3)):
    """
    Searches for clubs by a given query string.

    :param query: The search term (must be at least 3 characters long).
    :return: A list of clubs matching the search query.
    """
    return await search_clubs(query)


@app.get(
    "/api/club/{club_id}",
    response_model=FullClubInfoResponse,
    dependencies=[Depends(get_api_key)],
)
async def read_full_club_info(club_id: str):
    """
    Retrieves all available information for a club in a single response.

    This includes:
    - Club-level next and previous games.
    - A list of all teams.
    - For each team: its league table, next games, and previous games.

    :param club_id: The unique ID of the club from fussball.de.
    :return: A comprehensive club information object.
    """
    from .cache import club_info_cache
    cached = club_info_cache.get(club_id)
    if cached:
        logger.debug(f"Serving FullClubInfoResponse for {club_id} from object cache")
        return cached

    # First, get the list of teams for the club.
    teams_list = await get_club_teams(club_id)

    # Prepare all other data fetching tasks.
    tasks = [
        get_club_next_games(club_id),
        get_club_prev_games(club_id),
    ]
    for team in teams_list:
        tasks.append(get_team_table(team.id))
        tasks.append(get_team_next_games(team.id))
        tasks.append(get_team_prev_games(team.id))

    # Execute all tasks concurrently.
    results = await asyncio.gather(*tasks)

    # Unpack the results.
    club_next_games = results[0]
    club_prev_games = results[1]
    team_data_results = results[2:]

    # Assemble the detailed team information.
    teams_with_details = []
    for i, team in enumerate(teams_list):
        table = team_data_results[i * 3]
        next_games = team_data_results[i * 3 + 1]
        prev_games = team_data_results[i * 3 + 2]

        team_details = TeamWithDetails(
            **team.model_dump(),
            table=table,
            next_games=next_games,
            prev_games=prev_games,
        )
        teams_with_details.append(team_details)

    return FullClubInfoResponse(
        club_next_games=club_next_games,
        club_prev_games=club_prev_games,
        teams=teams_with_details,
    )


@app.get(
    "/api/club/{club_id}/teams",
    response_model=List[Team],
    dependencies=[Depends(get_api_key)],
)
async def read_club_teams(club_id: str):
    """
    Retrieves all teams for a given club ID.

    :param club_id: The unique ID of the club from fussball.de.
    :return: A list of teams.
    """
    from .cache import club_info_cache
    cached = club_info_cache.get(club_id)
    if cached:
        logger.debug(f"Serving teams for club {club_id} from object cache")
        return [Team(**team.model_dump()) for team in cached.teams]
    return await get_club_teams(club_id)


@app.get(
    "/api/club/{club_id}/info",
    response_model=ClubInfoResponse,
    dependencies=[Depends(get_api_key)],
)
async def read_club_info(club_id: str):
    """
    Retrieves combined information for a club, including teams,
    next games, and previous games.

    :param club_id: The unique ID of the club from fussball.de.
    :return: Combined club information.
    """
    from .cache import club_info_cache
    cached = club_info_cache.get(club_id)
    if cached:
        logger.debug(f"Serving ClubInfoResponse for {club_id} from object cache")
        return ClubInfoResponse(
            teams=[Team(**team.model_dump(exclude={"table","prev_games","next_games"})) for team in cached.teams],
            next_games=cached.club_next_games,
            prev_games=cached.club_prev_games,
        )
    teams_task = get_club_teams(club_id)
    next_games_task = get_club_next_games(club_id)
    prev_games_task = get_club_prev_games(club_id)

    teams, next_games, prev_games = await asyncio.gather(
        teams_task, next_games_task, prev_games_task
    )

    return ClubInfoResponse(
        teams=teams,
        next_games=next_games,
        prev_games=prev_games,
    )


@app.get(
    "/api/team/{team_id}",
    response_model=TeamInfoResponse,
    dependencies=[Depends(get_api_key)],
)
async def read_team_info(team_id: str):
    """
    Retrieves combined information for a team, including the league table,
    next games, and previous games.

    :param team_id: The unique ID of the team from fussball.de.
    :return: Combined team information.
    """
    from .cache import club_info_cache
    for club_id, cached in club_info_cache.items():
        for team in cached.teams:
            if team.id == team_id:
                logger.debug(f"Serving TeamInfoResponse for {team_id} from object cache")
                return TeamInfoResponse(
                    table=team.table,
                    prev_games=team.prev_games,
                    next_games=team.next_games,
                )
    table_task = get_team_table(team_id)
    next_games_task = get_team_next_games(team_id)
    prev_games_task = get_team_prev_games(team_id)

    table, next_games, prev_games = await asyncio.gather(
        table_task, next_games_task, prev_games_task
    )

    return TeamInfoResponse(
        table=table,
        next_games=next_games,
        prev_games=prev_games,
    )


@app.get(
    "/api/team/{team_id}/table",
    response_model=Table,
    dependencies=[Depends(get_api_key)],
)
async def read_team_table(team_id: str):
    """
    Retrieves the league table for a given team ID.

    :param team_id: The unique ID of the team from fussball.de.
    :return: The league table.
    :raises HTTPException: If no table is found for the team.
    """
    from .cache import club_info_cache
    for club_id, cached in club_info_cache.items():
        for team in cached.teams:
            if team.id == team_id and team.table is not None:
                logger.debug(f"Serving table for team {team_id} from object cache")
                return team.table
    table = await get_team_table(team_id)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Table not found for this team.",
        )
    return table


@app.get(
    "/api/club/{club_id}/next_games",
    response_model=List[Game],
    dependencies=[Depends(get_api_key)],
)
async def read_club_next_games(club_id: str):
    """
    Retrieves the upcoming games for all teams of a given club.

    :param club_id: The unique ID of the club from fussball.de.
    :return: A list of games.
    """
    from .cache import club_info_cache
    cached = club_info_cache.get(club_id)
    if cached:
        logger.debug(f"Serving club_next_games for {club_id} from object cache")
        return cached.club_next_games
    return await get_club_next_games(club_id)


@app.get(
    "/api/club/{club_id}/prev_games",
    response_model=List[Game],
    dependencies=[Depends(get_api_key)],
)
async def read_club_prev_games(club_id: str):
    """
    Retrieves the past games for all teams of a given club.

    :param club_id: The unique ID of the club from fussball.de.
    :return: A list of games.
    """
    from .cache import club_info_cache
    cached = club_info_cache.get(club_id)
    if cached:
        logger.debug(f"Serving club_prev_games for {club_id} from object cache")
        return cached.club_prev_games
    return await get_club_prev_games(club_id)


@app.get(
    "/api/team/{team_id}/next_games",
    response_model=List[Game],
    dependencies=[Depends(get_api_key)],
)
async def read_team_next_games(team_id: str):
    """
    Retrieves the upcoming games for a given team.

    :param team_id: The unique ID of the team from fussball.de.
    :return: A list of games.
    """
    from .cache import club_info_cache
    for club_id, cached in club_info_cache.items():
        for team in cached.teams:
            if team.id == team_id:
                logger.debug(f"Serving next_games for team {team_id} from object cache")
                return team.next_games
    return await get_team_next_games(team_id)


@app.get(
    "/api/team/{team_id}/prev_games",
    response_model=List[Game],
    dependencies=[Depends(get_api_key)],
)
async def read_team_prev_games(team_id: str):
    """
    Retrieves the past games for a given team.

    :param team_id: The unique ID of the team from fussball.de.
    :return: A list of games.
    """
    from .cache import club_info_cache
    for club_id, cached in club_info_cache.items():
        for team in cached.teams:
            if team.id == team_id:
                logger.debug(f"Serving prev_games for team {team_id} from object cache")
                return team.prev_games
    return await get_team_prev_games(team_id)


@app.get(
    "/api/game/{game_id}",
    response_model=Game,
    dependencies=[Depends(get_api_key)],
)
async def read_game_by_id(game_id: str):
    """
    Retrieves the details and match events for a single game.

    :param game_id: The unique game ID from fussball.de.
    :return: The Game object with details and match events.
    :raises HTTPException: If the game could not be fetched or parsed.
    """
    from .cache import club_info_cache

    # Try to serve from prewarmed object cache first
    for club_id, cached in club_info_cache.items():
        # Club-level games
        for g in cached.club_next_games + cached.club_prev_games:
            if g.id == game_id:
                logger.debug(f"Serving game {game_id} from object cache (club-level)")
                return g
        # Team-level games
        for team in cached.teams:
            for g in team.next_games + team.prev_games:
                if g.id == game_id:
                    logger.debug(f"Serving game {game_id} from object cache (team-level)")
                    return g

    game = await get_game_by_id(game_id)
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Game {game_id} not found or could not be parsed.",
        )
    return game
