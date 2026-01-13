import discord

VERSION = "dev14"
IS_ACTIVE = True
NAME = "PlayCord"
MANAGED_BY = "quantumbagel"
LOGGING_ROOT = "playcord"
SERVER_TIMEOUT = 5000

MESSAGE_COMMAND_FAILED = "⛔"
MESSAGE_COMMAND_SUCCEEDED = "✅"

MESSAGE_COMMAND_DISABLE = "disable"
MESSAGE_COMMAND_ENABLE = "enable"
MESSAGE_COMMAND_TOGGLE = "toggle"
MESSAGE_COMMAND_SYNC = "sync"
MESSAGE_COMMAND_CLEAR = "clear"
MESSAGE_COMMAND_SPECIFY_LOCAL_SERVER = "this"

OWNERS = [897146430664355850, 1085939954758205561]
CONFIGURATION = {}

WELCOME_MESSAGE = [
    (f"Hi! I'm {NAME}!", "Thanks for adding me to your server :D\nHere's some tips on how to get started.\n"
                         "Please note that this introduction (or the bot) doesn't contain details on how to"
                         " use the bot. For that, please check the README (linked below)."),
    ("What is this bot?", f"{NAME} is a bot for playing any variety of quick game on Discord."),
    ("Where's the README?", "Right [here](https://github.com/PlayCord/bot/blob/master/README.md) :D"),
    ("Who made you?", "[@quantumbagel on Github](https://github.com/quantumbagel)")
]

EMBED_COLOR = discord.Color.from_str("#6877ED")
ERROR_COLOR = discord.Color.from_str("#ED6868")
INFO_COLOR = discord.Color.from_str("#9A9CB0")

CONFIG_BOT_SECRET = "secret"

CONFIG_FILE = "configuration/config.yaml"
EMOJI_CONFIGURATION_FILE = "configuration/emoji.yaml"

ERROR_IMPORTED = "This file is NOT designed to be imported. Please run bot.py directly!"
ERROR_NO_SYSTEM_CHANNEL = "No system channel is set - not sending anything."
ERROR_INCORRECT_SETUP = ("This is likely due to:\n"
                         "1. Internet issues\n"
                         "2. Incorrect discord token\n"
                         "3. Incorrectly set up discord bot")

GAME_TYPES = {"tictactoe": ["games.TicTacToe", "TicTacToeGame"], "liars": ["games.LiarsDice", "LiarsDiceGame"],
              "test": ["games.TestGame", "TestGame"]}

MU = 1000
GAME_TRUESKILL = {"tictactoe": {"sigma": 1 / 6,
                                "beta": 1 / 12,
                                "tau": 1 / 100,
                                "draw": 9 / 10},
                  "liars": {"sigma": 1 / 2.5,
                            "beta": 1 / 5,
                            "tau": 1 / 250,
                            "draw": 0},
                  "test": {"sigma": 1 / 3, "beta": 1 / 5, "tau": 1 / 250, "draw": 0}}

TEXTIFY_CURRENT_GAME_TURN = {
    "It's {player}'s turn to play.": 0.529,
    "Next up: {player}.": 0.45,
    "We checked the books, and it is *somehow* {player}'s turn to play. Not sure how that happened.": 0.01,
    "After journeying the Himalayas for many a year, we now know that it's {player}'s turn!": 0.01,
    "Did you know that the chance of this turn message appearing is 0.1%?. alsobythewayit's{player}'sturn": 0.001

}

# Textify options for game started messages
TEXTIFY_GAME_STARTED = {
    "The game has begun! Good luck, {players}!": 0.5,
    "Let the games begin! {players}, may the best player win!": 0.3,
    "Game on! {players} are ready to battle it out!": 0.15,
    "Alright {players}, let's see what you've got!": 0.04,
    "In a world where only one can win... {players} enter the arena.": 0.01,
}

# Textify options for join button text
TEXTIFY_BUTTON_JOIN = {
    "Join": 0.7,
    "Join Game": 0.2,
    "Count me in!": 0.08,
    "I'm in!": 0.02,
}

# Textify options for leave button text
TEXTIFY_BUTTON_LEAVE = {
    "Leave": 0.7,
    "Leave Game": 0.2,
    "Nah, I'm out": 0.08,
    "Goodbye!": 0.02,
}

# Textify options for start button text
TEXTIFY_BUTTON_START = {
    "Start": 0.7,
    "Start Game": 0.2,
    "Let's go!": 0.08,
    "Begin!": 0.02,
}

# Textify options for game over messages
TEXTIFY_GAME_OVER = {
    "Game over! {winner} wins!": 0.4,
    "And the winner is... {winner}!": 0.3,
    "Congratulations to {winner} for the victory!": 0.2,
    "{winner} has emerged victorious!": 0.08,
    "Against all odds, {winner} has won! What a game!": 0.02,
}

# Textify options for draw messages
TEXTIFY_GAME_DRAW = {
    "It's a draw!": 0.5,
    "The game ends in a tie!": 0.3,
    "No winner this time - it's a draw!": 0.15,
    "Both players are evenly matched! It's a tie!": 0.05,
}

SIGMA_RELATIVE_UNCERTAINTY_THRESHOLD = 0.20

# Current ongoing games
# Format:
# {game thread id: GameInterface object}
CURRENT_GAMES = {}
CURRENT_MATCHMAKING = {}

IN_GAME = {}  # user id: gameinterface
IN_MATCHMAKING = {}  # user id: matchmakinginterface

AUTOCOMPLETE_CACHE = {}

# game_id
# - user_id
# - - current: autocompletes

DATABASE_GAME_IDS = {}

# Cross-server matchmaking queue
# Format: {game_type: {matchmaker_id: MatchmakingInterface}}
GLOBAL_MATCHMAKING_QUEUE = {}

# Whether to allow cross-server matchmaking (can be toggled)
CROSS_SERVER_MATCHMAKING_ENABLED = False

LONG_SPACE_EMBED = "\u2800"  # discord hides spaces when there is more than one in a row, this fixes it

# Button custom_id prefixes
BUTTON_PREFIX_JOIN = "join/"
BUTTON_PREFIX_LEAVE = "leave/"
BUTTON_PREFIX_START = "start/"
BUTTON_PREFIX_SELECT_CURRENT = "select_c/"
BUTTON_PREFIX_SELECT_NO_TURN = "select_n/"
BUTTON_PREFIX_CURRENT_TURN = "c/"
BUTTON_PREFIX_NO_TURN = "n/"
BUTTON_PREFIX_INVITE = "invite/"
BUTTON_PREFIX_SPECTATE = "spectate/"
BUTTON_PREFIX_PEEK = "peek/"

PRESENCE_TIMEOUT = 60
PRESENCE_PRESETS = [
    f"with {NAME}!",
    "games with friends!",
    "/play catalog"
]
