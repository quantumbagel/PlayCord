import logging
import sys

from discord import app_commands
from discord.ext import commands
from ruamel.yaml import YAML

import configuration.constants as constants
from cogs.games import begin_game, handle_autocomplete, handle_move  # For exec context
from configuration.constants import *
from utils import database as db
from utils.analytics import Timer
from utils.command_builder import build_function_definitions
from utils.discord_utils import command_error, interaction_check
from utils.formatter import Formatter

# Logging setup
logging.getLogger("discord").setLevel(logging.INFO)
logging.basicConfig(level=logging.DEBUG)
root_logger = logging.getLogger("root")
root_logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.DEBUG)
ch.setFormatter(Formatter())
root_logger.handlers = [ch]

log = logging.getLogger(LOGGING_ROOT)
startup_logger = log.getChild("startup")

startup_logger.info(f"Welcome to {NAME} by @quantumbagel!")
startup_initial_time = Timer().start()


def load_configuration() -> dict | None:
    begin_load_config = Timer().start()
    try:
        loaded_config_file = YAML().load(open(CONFIG_FILE))
    except FileNotFoundError:
        startup_logger.critical("Configuration file not found.")
        return
    startup_logger.debug(f"Successfully loaded configuration file in {begin_load_config.current_time}ms!")
    return loaded_config_file


config = load_configuration()
if config is None:
    sys.exit(1)
constants.CONFIGURATION = config

database_startup_time = Timer().start()
if not db.startup():
    startup_logger.critical("Database failed to connect on startup!")
    sys.exit(1)
else:
    startup_logger.info(f"Database startup completed in {database_startup_time.current_time}ms.")


class PlayCordBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        # Load Cogs
        await self.load_extension("cogs.general")
        await self.load_extension("cogs.matchmaking")
        await self.load_extension("cogs.games")
        await self.load_extension("cogs.events")
        await self.load_extension("cogs.admin")

        # Set up tree error handler
        self.tree.on_error = command_error

        # Dynamic command registration
        play_group = app_commands.Group(name="play", description="All of the games of PlayCord.", guild_only=True)
        play_group.interaction_check = interaction_check

        # In discord.py, you use decorators for error handlers on groups.
        # Since we have a global tree handler, we don't necessarily need it here,
        # but if we want it:
        @play_group.error
        async def play_group_error(interaction, error):
            await command_error(interaction, error)

        dynamic_commands = build_function_definitions(play_group)

        for group in dynamic_commands:
            self.tree.add_command(group)

            # Shared globals for all commands in this group (so autocomplete callbacks can be found)
            group_exec_globals = {
                'discord': discord,
                'app_commands': app_commands,
                'group': group,
                'handle_move': handle_move,
                'handle_autocomplete': handle_autocomplete,
                'begin_game': begin_game
            }

            for command_str in dynamic_commands[group]:
                try:
                    exec(command_str, group_exec_globals)
                except Exception as e:
                    startup_logger.error(f"Failed to register dynamic command:\n{command_str}\nError: {e}")

        # Add command_root group from GeneralCog manually as it is not built dynamically
        general_cog = self.get_cog("GeneralCog")
        if general_cog:
            if not any(c.name == general_cog.command_root.name for c in self.tree.get_commands()):
                self.tree.add_command(general_cog.command_root)


if __name__ == "__main__":
    bot = PlayCordBot()
    startup_logger.info(f"Starting bot after {startup_initial_time.current_time}ms")
    bot.run(config[CONFIG_BOT_SECRET], log_handler=None)
