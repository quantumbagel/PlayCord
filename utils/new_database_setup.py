import mysql.connector

from utils.database import (create_game, end_game, get_game_moves, get_game_participants,
                            get_or_create_gamemode,
                            get_rankings, get_server_id, get_server_metadata, get_total_matches_played,
                            get_user_gamemode_stats, get_user_id, get_user_last_games, get_user_metadata,
                            get_users_in_server, increment_total_matches_played, link_user_to_server,
                            update_server_metadata,
                            update_user_gamemode_stats,
                            update_user_metadata)  # Replace with actual filename

# Database setup
db_config = {
    'user': 'root',
    'password': 'password',
    'host': 'localhost',
    'database': 'grok_test'
}


def setup_database():
    """Create necessary tables for testing."""
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # # Drop existing tables
    cursor.execute("DROP TABLE IF EXISTS moves, games, gamemodes, servers, users")

    # Create tables
    cursor.execute("""
        CREATE TABLE users (
            user_id VARCHAR(255) PRIMARY KEY,
            ratings JSON,
            metadata JSON,
            servers JSON
        )
    """)
    cursor.execute("""
        CREATE TABLE servers (
            server_id VARCHAR(255) PRIMARY KEY,
            metadata JSON
        )
    """)
    cursor.execute("""
        CREATE TABLE gamemodes (
            gamemode_id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) UNIQUE
        )
    """)
    cursor.execute("""
        CREATE TABLE games (
            game_id VARCHAR(255) PRIMARY KEY,
            gamemode_id INT,
            version INT,
            participants JSON,
            result JSON,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP NULL,
            FOREIGN KEY (gamemode_id) REFERENCES gamemodes(gamemode_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE moves (
            game_id VARCHAR(255),
            user_id VARCHAR(255),
            turn_number INT,
            version INT,
            metadata JSON,
            PRIMARY KEY (game_id, turn_number, version),
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    """)
    conn.commit()
    conn.close()


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
