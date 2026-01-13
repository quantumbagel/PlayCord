import os

import discord

from configuration.constants import EMBED_COLOR, ERROR_COLOR
from utils.conversion import column_elo, column_names, column_turn, contextify
from utils.emojis import get_emoji_string


class CustomEmbed(discord.Embed):
    """
    A modified version of discord.Embed with two key changes:

    * respects the default embed color of constants.py
    * Adds a bagel footer by default
    """

    def __init__(self, **kwargs):
        """
        Initialize the embed.
        :param kwargs: Arguments to the discord.Embed constructor
        """

        if 'color' not in kwargs:
            kwargs['color'] = EMBED_COLOR
        super().__init__(**kwargs)  # Force a consistent embed color based on the config

        self.set_footer(text=f"Made with ❤ by @quantumbagel",
                        # Force bagel footer by default, this can be overriden tho
                        icon_url="https://avatars.githubusercontent.com/u/58365715")


class ErrorEmbed(discord.Embed):

    def __init__(self, ctx=None, what_failed=None, reason=None):
        current_directory = os.path.dirname(__file__).rstrip("utils")
        super().__init__(title=f"{get_emoji_string("facepalm")} Something went wrong!",
                         color=ERROR_COLOR)  # Force a consistent embed color based on the config
        self.add_field(name=f"{get_emoji_string("github")} Please report the issue on GitHub",
                       value="I would really appreciate if you reported this error (and a detailed description of what you did to cause it if possible) on the [GitHub issue tracker](https://github.com/PlayCord/bot/issues)")
        if ctx is not None:
            self.add_field(name=f"{get_emoji_string("clueless")} Context:", value="```" + contextify(ctx) + "```",
                           inline=False)
        if what_failed is not None:
            self.add_field(name=f"{get_emoji_string("explosion")} What failed?", value="```" + what_failed + "```",
                           inline=False)
        reason = reason.replace(current_directory, "")  # Remove the main part of the directory
        # (for obfuscation purposes)
        if reason is not None:
            text_fields = []
            running_total = 0
            temp_line = ""
            for line in reason.split("\n"):
                running_total += len(line) + 1
                if running_total <= 1017:  # = 1024 (field value limit) - 6 (backticks for proper formatting) - 1 (\n)
                    temp_line += line + "\n"
                else:
                    text_fields.append(temp_line + "\n")
                    temp_line = line
                    running_total = len(line) + 1
            text_fields.append(temp_line)

            for i in range(len(text_fields)):
                self.add_field(name=f"{get_emoji_string("hmm")} Reason (part {i + 1} of {len(text_fields)}):",
                               value="```" + text_fields[i] + "```",
                               inline=False)

        self.set_footer(text=f"Sorry for the inconvenience! Please report this issue on our GitHub page.")


class GameOverviewEmbed(CustomEmbed):

    def __init__(self, game_name, game_type, rated, players, turn):
        rated_text = "Rated" if rated else "Unrated"
        super().__init__(title=f"{rated_text} {game_name} game started!",
                         description="Click the button if you want to spectate the game, or just view the game's progress.",
                         color=EMBED_COLOR)  # Force a consistent embed color based on the config
        self.add_field(name="Players:", value=column_names(players), inline=True)
        self.add_field(name="Ratings:", value=column_elo(players, game_type), inline=True)
        self.add_field(name="Turn:", value=column_turn(players, turn), inline=True)


class GameOverEmbed(CustomEmbed):

    def __init__(self, rankings, game_name):
        super().__init__(title=f"{game_name} game over!",
                         description=f"Thanks so much for playing! Here are the rankings:")
        self.add_field(name="Rankings:", value=rankings, inline=True)


class InviteEmbed(CustomEmbed):
    def __init__(self, inviter, game_type, guild_name):
        super().__init__(
            title=f"👋 You've been invited!",
            description=f"{inviter.mention} has invited you to play a game of **{game_type}** in **{guild_name}**.",
            color=EMBED_COLOR
        )
        self.add_field(name="How to join?", value="Click the 'Join Game' button below to join the lobby.", inline=False)
        self.add_field(name="Note:", value="If you don't want to play, you can simply ignore this message.",
                       inline=False)
