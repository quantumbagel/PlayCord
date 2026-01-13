import json

import mysql.connector

# TODO: FIX THIS CRAP
# Database setup
db_config = {
    'user': 'root',
    'password': 'password',
    'host': 'localhost',
    'port': 33060,
    'database': 'playcord'
}


def setup_database():
    """Create necessary tables for testing."""
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # # Drop existing tables
    cursor.execute("DROP TABLE IF EXISTS moves, games, gamemodes, servers, users")

    # Create tables
    cursor.execute("""
                   CREATE TABLE users
                   (
                       user_id  VARCHAR(255) PRIMARY KEY,
                       ratings  JSON,
                       metadata JSON,
                       servers  JSON
                   )
                   """)
    cursor.execute("""
                   CREATE TABLE servers
                   (
                       server_id VARCHAR(255) PRIMARY KEY,
                       metadata  JSON
                   )
                   """)
    cursor.execute("""
                   CREATE TABLE gamemodes
                   (
                       gamemode_id INT AUTO_INCREMENT PRIMARY KEY,
                       name        VARCHAR(255) UNIQUE
                   )
                   """)
    cursor.execute("""
                   CREATE TABLE games
                   (
                       game_id      VARCHAR(255) PRIMARY KEY,
                       gamemode_id  INT,
                       version      INT,
                       participants JSON,
                       result       JSON,
                       start_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       end_time     TIMESTAMP NULL,
                       FOREIGN KEY (gamemode_id) REFERENCES gamemodes (gamemode_id)
                   )
                   """)
    cursor.execute("""
                   CREATE TABLE moves
                   (
                       game_id     VARCHAR(255),
                       user_id     VARCHAR(255),
                       turn_number INT,
                       version     INT,
                       metadata    JSON,
                       PRIMARY KEY (game_id, turn_number, version),
                       FOREIGN KEY (game_id) REFERENCES games (game_id)
                   )
                   """)
    conn.commit()
    conn.close()


def _execute_query(query, params=None, fetchone=False, fetchall=False, commit=False):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        if commit:
            conn.commit()
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        return cursor.lastrowid
    finally:
        cursor.close()
        conn.close()


def get_user_id(discord_id):
    user_id = str(discord_id)
    _execute_query("INSERT IGNORE INTO users (user_id, ratings, metadata, servers) VALUES (%s, %s, %s, %s)",
                   (user_id, json.dumps({}), json.dumps({}), json.dumps([])), commit=True)
    return user_id


def get_server_id(discord_guild_id):
    server_id = str(discord_guild_id)
    _execute_query("INSERT IGNORE INTO servers (server_id, metadata) VALUES (%s, %s)",
                   (server_id, json.dumps({})), commit=True)
    return server_id


def link_user_to_server(user_id, server_id):
    user = _execute_query("SELECT servers FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if user:
        servers = json.loads(user['servers'])
        if server_id not in servers:
            servers.append(server_id)
            _execute_query("UPDATE users SET servers = %s WHERE user_id = %s", (json.dumps(servers), user_id),
                           commit=True)


def get_users_in_server(server_id):
    # This is inefficient but fits the schema where users have list of servers
    all_users = _execute_query("SELECT user_id, servers FROM users", fetchall=True)
    users_in_server = []
    for user in all_users:
        if server_id in json.loads(user['servers']):
            users_in_server.append(user['user_id'])
    return users_in_server


def update_user_metadata(user_id, metadata_update):
    user = _execute_query("SELECT metadata FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if user:
        metadata = json.loads(user['metadata'])
        metadata.update(metadata_update)
        _execute_query("UPDATE users SET metadata = %s WHERE user_id = %s", (json.dumps(metadata), user_id),
                       commit=True)


def get_user_metadata(user_id):
    user = _execute_query("SELECT metadata FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    return json.loads(user['metadata']) if user else {}


def increment_total_matches_played(user_id, amount):
    metadata = get_user_metadata(user_id)
    current = metadata.get("total_matches_played", 0)
    metadata["total_matches_played"] = current + amount
    update_user_metadata(user_id, metadata)


def get_total_matches_played(user_id):
    metadata = get_user_metadata(user_id)
    return metadata.get("total_matches_played", 0)


def update_server_metadata(server_id, metadata_update):
    server = _execute_query("SELECT metadata FROM servers WHERE server_id = %s", (server_id,), fetchone=True)
    if server:
        metadata = json.loads(server['metadata'])
        metadata.update(metadata_update)
        _execute_query("UPDATE servers SET metadata = %s WHERE server_id = %s", (json.dumps(metadata), server_id),
                       commit=True)


def get_server_metadata(server_id):
    server = _execute_query("SELECT metadata FROM servers WHERE server_id = %s", (server_id,), fetchone=True)
    return json.loads(server['metadata']) if server else {}


def get_or_create_gamemode(name):
    _execute_query("INSERT IGNORE INTO gamemodes (name) VALUES (%s)", (name,), commit=True)
    res = _execute_query("SELECT gamemode_id FROM gamemodes WHERE name = %s", (name,), fetchone=True)
    return res['gamemode_id']


def update_user_gamemode_stats(user_id, gamemode_id, mu, sigma, matches):
    user = _execute_query("SELECT ratings FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if user:
        ratings = json.loads(user['ratings'])
        ratings[str(gamemode_id)] = {"mu": mu, "sigma": sigma, "matches": matches}
        _execute_query("UPDATE users SET ratings = %s WHERE user_id = %s", (json.dumps(ratings), user_id), commit=True)


def get_user_gamemode_stats(user_id, gamemode_id):
    user = _execute_query("SELECT ratings FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if user:
        ratings = json.loads(user['ratings'])
        stat = ratings.get(str(gamemode_id), {"mu": 25.0, "sigma": 8.333, "matches": 0})
        return stat['mu'], stat['sigma'], stat['matches']
    return 25.0, 8.333, 0


def create_game(game_id, participants, gamemode_name):
    gamemode_id = get_or_create_gamemode(gamemode_name)
    _execute_query(
        "INSERT INTO games (game_id, gamemode_id, version, participants) VALUES (%s, %s, %s, %s)",
        (game_id, gamemode_id, 1, json.dumps([str(p) for p in participants])),
        commit=True
    )
    return game_id


def end_game(game_id, moves, result):
    # result should contain rating updates
    _execute_query(
        "UPDATE games SET result = %s, end_time = CURRENT_TIMESTAMP WHERE game_id = %s",
        (json.dumps(result), game_id),
        commit=True
    )
    # Record moves
    for move in moves:
        _execute_query(
            "INSERT INTO moves (game_id, user_id, turn_number, version, metadata) VALUES (%s, %s, %s, %s, %s)",
            (game_id, str(move['user_id']) if move['user_id'] else None, move['turn_number'], 1,
             json.dumps(move['metadata'])),
            commit=True
        )
    # Typically end_game would also update user ratings based on result
    if "ratings" in result:
        res = _execute_query("SELECT gamemode_id FROM games WHERE game_id = %s", (game_id,), fetchone=True)
        if res:
            gamemode_id = res['gamemode_id']
            for user_id, updates in result['ratings'].items():
                mu, sigma, matches = get_user_gamemode_stats(user_id, gamemode_id)
                update_user_gamemode_stats(user_id, gamemode_id, updates['new_mu'], updates['new_sigma'], matches + 1)


def get_rankings(game_id, server_id=None, local=False):
    game = _execute_query("SELECT result FROM games WHERE game_id = %s", (game_id,), fetchone=True)
    if game and game['result']:
        result = json.loads(game['result'])
        return result.get("rankings", {})
    return {}


def get_user_last_games(user_id, limit=10):
    # This check is expensive with JSON participants
    all_games = _execute_query("SELECT game_id, participants, start_time FROM games ORDER BY start_time DESC",
                               fetchall=True)
    user_games = []
    user_id_str = str(user_id)
    for g in all_games:
        if user_id_str in json.loads(g['participants']):
            user_games.append(g['game_id'])
            if len(user_games) >= limit:
                break
    return user_games


def get_game_moves(game_id):
    return _execute_query("SELECT * FROM moves WHERE game_id = %s ORDER BY turn_number ASC", (game_id,), fetchall=True)


def get_game_participants(game_id):
    res = _execute_query("SELECT participants FROM games WHERE game_id = %s", (game_id,), fetchone=True)
    return json.loads(res['participants']) if res else []


def run_tests():
    """Run a series of tests on the database interface."""
    print("Setting up database...")
    setup_database()

    # Connect to database
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    print("Connected to database")

    # Test 1: User and Server Management
    print("\nTest 1: User and Server Management")
    user_id = get_user_id(123)
    print(f"Created/fetched user: {user_id}")
    server_id = get_server_id(456)
    print(f"Created/fetched server: {server_id}")
    link_user_to_server(user_id, server_id)
    users_in_server = get_users_in_server(server_id)
    print(f"Users in server {server_id}: {users_in_server}")

    # Test 2: Metadata Management
    print("\nTest 2: Metadata Management")
    update_user_metadata(user_id, {"total_matches_played": 5, "nickname": "test_user"})
    metadata = get_user_metadata(user_id)
    print(f"User metadata: {metadata}")
    increment_total_matches_played(user_id, 2)
    print(f"Total matches played after increment: {get_total_matches_played(user_id)}")
    update_server_metadata(server_id, {"name": "Test Server"})
    print(f"Server metadata: {get_server_metadata(server_id)}")

    # Test 3: Game Mode and Ratings
    print("\nTest 3: Game Mode and Ratings")
    gamemode_id = get_or_create_gamemode("chess")
    print(f"Game mode ID for chess: {gamemode_id}")
    update_user_gamemode_stats(user_id, gamemode_id, 30.0, 7.5, 3)
    mu, sigma, matches = get_user_gamemode_stats(user_id, gamemode_id)
    print(f"User stats for chess - mu: {mu}, sigma: {sigma}, matches: {matches}")

    # Test 4: Game Operations
    print("\nTest 4: Game Operations")
    game_id = create_game("game1", [123], "chess")
    print(f"Created game: {game_id}")
    moves = [
        {"user_id": 123, "turn_number": 1, "metadata": {"move": "e4"}},
        {"user_id": None, "turn_number": 2, "metadata": {"move": "e5"}}
    ]
    result = {
        "ratings": {
            "123": {"new_mu": 32.0, "new_sigma": 7.0},
        },
        "rankings": {"123": 1}
    }
    end_game(game_id, moves, result)
    print(f"Ended game {game_id}")

    # Test 5: Rankings and History
    print("\nTest 5: Rankings and History")
    rankings = get_rankings(game_id, server_id=server_id, local=True)
    print(f"Local rankings: {rankings}")
    last_games = get_user_last_games(user_id, limit=1)
    print(f"Last game: {last_games}")
    game_moves = get_game_moves(game_id)
    print(f"Game moves: {game_moves}")
    participants = get_game_participants(game_id)
    print(f"Game participants: {participants}")

    # Cleanup
    conn.close()
    print("\nTests completed")


if __name__ == "__main__":
    try:
        run_tests()
    except mysql.connector.Error as err:
        print(f"Database error: {err}")
    except Exception as e:
        print(f"Error: {e}")
