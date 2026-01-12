import json
import logging
from datetime import datetime

import discord
import mysql.connector
from mysql.connector import Error

from api.Player import Player
from configuration import constants
from configuration.constants import GAME_TRUESKILL, MU, SIGMA_RELATIVE_UNCERTAINTY_THRESHOLD

logger = logging.getLogger("playcord.database")


class InternalPlayerRatingStatistic:
    def __init__(self, name, mu, sigma):
        self.name = name
        if mu is None:
            self.mu = MU
            self.sigma = GAME_TRUESKILL[name]["sigma"] * MU
            self.stored = False
        else:
            self.mu = mu
            self.sigma = sigma
            self.stored = True


class InternalPlayer:
    def __init__(self, ratings: dict[str, dict[str, float]], user: discord.User | discord.Object = None, metadata=None,
                 id: int = None):
        # Null checks
        if isinstance(user, discord.User):
            self.name = user.name
        else:
            self.name = None

        if user is not None:
            self.id = user.id
        else:
            self.id = id

        if metadata is not None:
            self.metadata = metadata
        else:
            self.metadata = {}

        # No servers in new schema
        self.servers = []

        # Blind assignments
        self.user = user
        self.ratings = ratings
        self.player_data = {}
        self.moves_made = 0

        self._update_ratings(self.ratings)

    def _update_ratings(self, ratings):
        for key in GAME_TRUESKILL:
            if key not in ratings:
                ratings[key] = {"mu": MU, "sigma": GAME_TRUESKILL[key]["sigma"] * MU}
            setattr(self, key, InternalPlayerRatingStatistic(key,
                                                             ratings[key]["mu"],
                                                             ratings[key]["sigma"]))

    @property
    def mention(self):
        return f"<@{self.id}>"  # Don't use self.user.mention because it could be an Object

    def get_formatted_elo(self, game_type):
        rating = getattr(self, game_type)
        if rating.mu is None:  # No rating information
            return "No Rating"
        if rating.sigma > SIGMA_RELATIVE_UNCERTAINTY_THRESHOLD * rating.mu:
            return str(round(rating.mu)) + "?"
        return str(round(rating.mu))

    def __eq__(self, other):
        if other is None:
            return False
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __str__(self):
        if self.user is not None:
            return f"{self.id} ({self.name}) bot={self.user.bot} ratings={self.ratings}"
        else:
            return f"{self.id} ({self.name}) bot=no-user-provided, ratings={self.ratings}"

    def __repr__(self):
        if self.user is not None:
            return f"InternalPlayer(id={self.id}, bot={self.user.bot}, ratings={self.ratings})"
        else:
            return f"InternalPlayer(id={self.id}, bot=no-user-provided, ratings={self.ratings})"


def get_shallow_player(user: discord.User) -> InternalPlayer:
    return InternalPlayer(ratings={}, user=user)


def internal_player_to_player(internal_player: InternalPlayer, game_type: str) -> Player:
    rating = getattr(internal_player, game_type)
    return Player(mu=rating.mu, sigma=rating.sigma, ranking=None,
                  id=internal_player.user.id, name=internal_player.user.name)


class Database:
    def __init__(self, host, user, password, database):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.connection = None
        self.connect()

    def connect(self):
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database
            )
            logger.info("Connected to database.")
        except Error as e:
            logger.info(f"Error connecting to MySQL: {e}")
            self.connection = None

    def _execute_query(self, query, params=None, fetchone=False, fetchall=False):
        if not self.connection:
            self.connect()
            if not self.connection:
                raise Exception("Failed to connect to database.")

        cursor = self.connection.cursor(dictionary=True)
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if fetchone:
                return cursor.fetchone()
            elif fetchall:
                return cursor.fetchall()
            else:
                self.connection.commit()
                return cursor.lastrowid if cursor.lastrowid else None
        except Error as e:
            logger.warning(
                f"Error executing query {query} (params={params}, fetchone={fetchone}, fetchall={fetchall}): {e}")
            raise
        finally:
            cursor.close()

    def create_user(self, user_id):
        query = "INSERT IGNORE INTO users (user_id) VALUES (%s);"
        self._execute_query(query, (user_id,))

    def create_guild(self, guild_id):
        query = "INSERT IGNORE INTO guilds (guild_id) VALUES (%s);"
        self._execute_query(query, (guild_id,))

    def update_user_preferences(self, user_id, preferences):
        preferences_json = json.dumps(preferences)
        query = """
                INSERT INTO users (user_id, preferences)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE preferences = VALUES(preferences);
                """
        self._execute_query(query, (user_id, preferences_json))

    def get_user_preferences(self, user_id):
        query = "SELECT joined_at, preferences FROM users WHERE user_id = %s;"
        result = self._execute_query(query, (user_id,), fetchone=True)
        if result and result['preferences']:
            result['preferences'] = json.loads(result['preferences'])
        return result

    def update_guild_preferences(self, guild_id, preferences):
        preferences_json = json.dumps(preferences)
        query = """
                INSERT INTO guilds (guild_id, preferences)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE preferences = VALUES(preferences);
                """
        self._execute_query(query, (guild_id, preferences_json))

    def get_guild_preferences(self, guild_id):
        query = "SELECT joined_at, preferences FROM guilds WHERE guild_id = %s;"
        result = self._execute_query(query, (guild_id,), fetchone=True)
        if result and result['preferences']:
            result['preferences'] = json.loads(result['preferences'])
        return result

    def delete_user(self, user_id):
        query = "DELETE FROM users WHERE user_id = %s;"
        self._execute_query(query, (user_id,))

    def initialize_user_game_ratings(self, user_id, guild_id, game_name):
        self.create_user(user_id)
        self.create_guild(guild_id)
        mu = MU
        sigma = GAME_TRUESKILL[game_name]["sigma"] * mu
        query = "INSERT IGNORE INTO user_game_ratings (user_id, guild_id, game_name, mu, sigma) VALUES (%s, %s, %s, %s, %s);"
        self._execute_query(query, (user_id, guild_id, game_name, mu, sigma))

    def update_ratings_after_match(self, user_id, guild_id, game_name, mu, sigma,
                                   matches_played_increment=1):
        self.create_user(user_id)
        self.create_guild(guild_id)
        query = """
                INSERT INTO user_game_ratings (user_id, guild_id, game_name, mu, sigma, matches_played, last_played)
                VALUES (%s, %s, %s, 25.0 + %s, 8.333 + %s, %s, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE mu             = %s,
                                        sigma          = %s,
                                        matches_played = matches_played + %s,
                                        last_played    = CURRENT_TIMESTAMP;
                """
        self._execute_query(query,
                            (user_id, guild_id, game_name, mu, sigma, matches_played_increment, mu,
                             sigma,
                             matches_played_increment))

    def get_user_game_ratings(self, user_id, guild_id, game_name):
        query = "SELECT mu, sigma, matches_played, last_played FROM user_game_ratings WHERE user_id = %s AND guild_id = %s AND game_name = %s;"
        return self._execute_query(query, (user_id, guild_id, game_name), fetchone=True)

    def get_game_leaderboard(self, guild_id, game_name, limit=10):
        query = """
                SELECT user_id, mu, sigma, conservative_rating AS rating, matches_played
                FROM user_game_ratings
                WHERE guild_id = %s
                  AND game_name = %s
                ORDER BY conservative_rating DESC
                LIMIT %s;
                """
        return self._execute_query(query, (guild_id, game_name, limit), fetchall=True)

    def reset_user_game_ratings(self, user_id, guild_id, game_name):
        query = """
                UPDATE user_game_ratings
                SET mu             = 25.0,
                    sigma          = 8.333,
                    matches_played = 0,
                    last_played    = NULL
                WHERE user_id = %s
                  AND guild_id = %s
                  AND game_name = %s;
                """
        self._execute_query(query, (user_id, guild_id, game_name))

    def delete_user_game_ratings(self, user_id, guild_id, game_name):
        query = "DELETE FROM user_game_ratings WHERE user_id = %s AND guild_id = %s AND game_name = %s;"
        self._execute_query(query, (user_id, guild_id, game_name))

    def record_new_game(self, game_name, guild_id, started_at, is_rated, game_data):
        self.create_guild(guild_id)
        ended_at = datetime.now()
        game_data_json = json.dumps(game_data)
        query = """
                INSERT INTO matches (game_name, guild_id, started, ended, rated, game_data)
                VALUES (%s, %s, %s, %s, %s, %s);
                """
        params = (game_name, guild_id, started_at, ended_at, is_rated, game_data_json)
        return self._execute_query(query, params)

    def get_match_details(self, match_id):
        query = "SELECT * FROM matches WHERE match_id = %s;"
        result = self._execute_query(query, (match_id,), fetchone=True)
        if result and result['game_data']:
            result['game_data'] = json.loads(result['game_data'])
        return result

    def get_recent_matches_for_game(self, guild_id, game_name, limit=10):
        query = """
                SELECT match_id, started, ended, rated
                FROM matches
                WHERE guild_id = %s
                  AND game_name = %s
                ORDER BY ended DESC
                LIMIT %s;
                """
        return self._execute_query(query, (guild_id, game_name, limit), fetchall=True)

    def update_match_game_data(self, match_id, new_final_scores):
        new_final_scores_json = json.dumps(new_final_scores)
        query = """
                UPDATE matches
                SET game_data = JSON_SET(game_data, '$.final_scores', %s)
                WHERE match_id = %s;
                """
        self._execute_query(query, (new_final_scores_json, match_id))

    def delete_match(self, match_id):
        query = "DELETE FROM matches WHERE match_id = %s;"
        self._execute_query(query, (match_id,))

    def add_match_participants(self, match_id, participants):
        query = """
                INSERT INTO match_participants (match_id, user_id, ranking, mu_delta, sigma_delta)
                VALUES (%s, %s, %s, %s, %s);
                """
        for participant_key in participants:
            p = participants[participant_key]
            self.create_user(p['uid'])
            self._execute_query(query, (match_id, p['uid'], p['ranking'], p['mu_delta'], p['sigma_delta']))

    def get_match_participants(self, match_id):
        query = """
                SELECT user_id, ranking, mu_delta, sigma_delta
                FROM match_participants
                WHERE match_id = %s
                ORDER BY ranking ASC;
                """
        return self._execute_query(query, (match_id,), fetchall=True)

    def get_user_match_history(self, user_id, guild_id, limit=10):
        query = """
                SELECT m.match_id, m.game_name, m.ended, mp.ranking, mp.mu_delta, mp.sigma_delta
                FROM match_participants mp
                         JOIN matches m ON mp.match_id = m.match_id
                WHERE mp.user_id = %s
                  AND m.guild_id = %s
                ORDER BY m.ended DESC
                LIMIT %s;
                """
        return self._execute_query(query, (user_id, guild_id, limit), fetchall=True)

    def get_inactive_users(self, guild_id=None):
        query = """
                SELECT guild_id, user_id, game_name, last_played
                FROM user_game_ratings
                WHERE last_played < DATE_SUB(CURRENT_TIMESTAMP, INTERVAL 30 DAY)
                """
        params = []
        if guild_id is not None:
            query += " AND guild_id = %s"
            params.append(guild_id)
        return self._execute_query(query, tuple(params) if params else None, fetchall=True)

    def get_full_match_details(self, match_id):
        query = """
                SELECT m.*, mp.user_id, mp.ranking, mp.mu_delta, mp.sigma_delta, ugr.mu, ugr.sigma
                FROM matches m
                         JOIN match_participants mp ON m.match_id = mp.match_id
                         LEFT JOIN user_game_ratings ugr
                                   ON mp.user_id = ugr.user_id AND m.game_name = ugr.game_name AND
                                      m.guild_id = ugr.guild_id
                WHERE m.match_id = %s;
                """
        results = self._execute_query(query, (match_id,), fetchall=True)
        for result in results:
            if result['game_data']:
                result['game_data'] = json.loads(result['game_data'])
        return results

    def count_matches_for_game(self, guild_id, game_name, is_rated=None):
        query = """
                SELECT COUNT(*) AS match_count
                FROM matches
                WHERE guild_id = %s
                  AND game_name = %s
                """
        params = [guild_id, game_name]
        if is_rated is not None:
            query += " AND rated = %s"
            params.append(is_rated)
        query += ";"
        result = self._execute_query(query, tuple(params), fetchone=True)
        return result['match_count'] if result else 0

    def count_matches_for_user(self, user_id, guild_id, is_rated=None):
        query = """
                SELECT COUNT(DISTINCT m.match_id) AS total_matches
                FROM match_participants mp
                         JOIN matches m ON mp.match_id = m.match_id
                WHERE mp.user_id = %s
                  AND m.guild_id = %s
                """
        params = [user_id, guild_id]
        if is_rated is not None:
            query += " AND m.rated = %s"
            params.append(is_rated)
        query += ";"
        result = self._execute_query(query, tuple(params), fetchone=True)
        return result['total_matches'] if result else 0

    def get_player(self, user: discord.User | discord.Member, guild_id: int) -> InternalPlayer | None:
        """
        Get an InternalPlayer object from the database (per-guild ratings now).
        """
        user_id = user.id if isinstance(user, (discord.User, discord.Member, InternalPlayer)) else user
        preferences = self.get_user_preferences(user_id)
        metadata = preferences['preferences'] if preferences and preferences['preferences'] else {}

        query = "SELECT game_name, mu, sigma FROM user_game_ratings WHERE user_id = %s AND guild_id = %s;"
        ratings_data = self._execute_query(query, (user_id, guild_id), fetchall=True)
        ratings = {row['game_name']: {'mu': row['mu'], 'sigma': row['sigma']} for row in
                   ratings_data} if ratings_data else {}

        ip = InternalPlayer(
            ratings=ratings,
            user=user if isinstance(user, (discord.User, discord.Member)) else None,
            metadata=metadata,
            id=user_id
        )
        return ip

    def create_game(self, game_name: str, guild_id: int, participants: list,
                    is_rated: bool = True) -> int:
        self.create_guild(guild_id)
        user_ids = [p.id if hasattr(p, 'id') else p for p in participants]
        for user_id in user_ids:
            self.create_user(user_id)
            self.initialize_user_game_ratings(user_id, guild_id, game_name)

        started_at = datetime.now()

        match_id = self.record_new_game(
            game_name=game_name,
            guild_id=guild_id,
            started_at=started_at,
            is_rated=is_rated,
            game_data={"status": "in_progress"}
        )

        return match_id

    def end_game(self, match_id: int, game_name: str, final_scores: dict[int, float],
                 rating_updates: dict):
        match = self.get_match_details(match_id)
        if not match:
            raise ValueError(f"Match {match_id} not found.")
        guild_id = match['guild_id']

        self.update_match_game_data(match_id, final_scores)

        self.add_match_participants(match_id, rating_updates)

        for update_key, actual_update in rating_updates.items():
            self.update_ratings_after_match(
                user_id=actual_update["uid"],
                guild_id=guild_id,
                game_name=game_name,
                mu=actual_update["new_mu"],
                sigma=actual_update["new_sigma"],
                matches_played_increment=1
            )


database: Database | None = None


def startup():
    global database
    config_db = constants.CONFIGURATION["db"]
    try:
        db = Database(
            host=config_db["host"],
            user=config_db["user"],
            password=config_db["password"],
            database=config_db["database"]
        )
        database = db
        return True
    except mysql.connector.Error as err:
        logger.error(f"Failed to connect to database: {err}")
        return False
