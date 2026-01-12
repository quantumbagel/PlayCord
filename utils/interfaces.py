import asyncio
import importlib
import inspect
import logging
import random
import traceback
import typing
from typing import Any

import trueskill

from api.Game import PlayerOrder
from api.Player import Player
from api.Response import Response
from configuration.constants import *
from utils import database as db
from utils.analytics import Timer
from utils.conversion import column_creator, column_elo, column_names, column_turn, contextify, player_representative, \
    player_verification_function, textify
from utils.database import InternalPlayer, get_shallow_player, internal_player_to_player
from utils.embeds import CustomEmbed, ErrorEmbed, GameOverEmbed, GameOverviewEmbed
from utils.emojis import get_emoji_string
from utils.views import MatchmakingView, SpectateView


class GameInterface:
    """
    A class that handles the interface between the game and discord

    Discord <--> Bot <--> GameInterface <--> Game
    """

    def __init__(self, game_type: str, status_message: discord.InteractionMessage, creator: discord.User,
                 players: list[InternalPlayer], rated: bool, game_id: int) -> None:
        """
        Create the GameInterface
        :param game_type: The game type as defined in constants.py
        :param status_message: The message already created by the bot outside the not-yet-existent thread
        :param creator: the User (discord) who created the lobby TODO: change to Player
        :param players: A list of Player.py objects representing the players
        :param rated: Whether the game is rated (ratings change based on outcome)
        :param game_id: The game ID in the database
        """
        # The message created by the bot outside the not-yet-existent thread
        self.game_id = game_id
        self.status_message = status_message
        # The game type
        self.game_type = game_type
        # Who created the lobby
        self.creator = creator

        # Get the game class to check player_order setting
        self.module = importlib.import_module(GAME_TYPES[game_type][0])  # Game module
        game_class = getattr(self.module, GAME_TYPES[game_type][1])

        # Order players based on game's player_order setting
        player_order = getattr(game_class, 'player_order', PlayerOrder.RANDOM)

        if player_order == PlayerOrder.RANDOM:
            random.shuffle(players)
        elif player_order == PlayerOrder.PRESERVE:
            pass  # Keep order as-is
        elif player_order == PlayerOrder.CREATOR_FIRST:
            # Move creator to front, shuffle the rest
            creator_player = None
            other_players = []
            for p in players:
                if p.id == creator.id:
                    creator_player = p
                else:
                    other_players.append(p)
            random.shuffle(other_players)
            if creator_player:
                players = [creator_player] + other_players
            else:
                players = other_players
        elif player_order == PlayerOrder.REVERSE:
            players = list(reversed(players))

        # All players in the game
        self.players = players
        self.rated = rated  # Is the game rated?
        self.thread = None  # The thread object after self.setup() is called
        self.game_message = None  # The message representing the game after self.setup() is called
        self.info_message = None  # The message showing game info, whose turn, and what players.
        # also made by self.setup()
        # Game class instantiated with the players
        self.game = (game_class
                     ([Player(mu=getattr(p, self.game_type).mu, sigma=getattr(p, self.game_type).sigma, ranking=None,
                              id=p.id) for p in players]))

        self.current_turn = None
        self.logger = logging.getLogger(f"GameInterface[{game_type}]")

        for p in players:
            IN_GAME.update({p: self})

        self.processing_move = asyncio.Lock()
        self.ending_game = False

    async def setup(self) -> None:
        """
        Sets up the game in Discord
        1. Create a private thread off of the channel the bot was called on
        2. Add users to the thread
        3. Send a message to the thread (that is used for the game message)

        Due to an async limitation, this function must be called on the class directly after it is created.
        :return: Nothing
        """
        log = self.logger.getChild("setup")
        setup_timer = Timer().start()
        log.debug(f"Setting up game interface for a new game. matchmaker ID: {self.status_message.id}")
        rated_prefix = "Rated "  # Add "Rated" to the name of the thread if the game
        if not self.rated:
            rated_prefix = ""

        game_thread = await self.status_message.channel.create_thread(  # Create the private thread.
            name=f"{rated_prefix}{self.game.name} game ({NAME})",
            type=discord.ChannelType.private_thread, invitable=False)  # Don't allow people to add themselves

        async def add_new_members_to_thread():
            for player in self.players:  # Add users to the thread
                await game_thread.add_user(player.user)

        asyncio.create_task(add_new_members_to_thread())  # Nonblocking

        game_info_embed = CustomEmbed(description=get_emoji_string("loading")).remove_footer()
        # Temporary embed TODO: remove this and make it cleaner while still guaranteeing the bot gets the first message
        getting_ready_embed = CustomEmbed(description=get_emoji_string("loading")).remove_footer()

        # Set the thread and game message in the class
        self.thread = game_thread

        async def send_game_messages():
            self.info_message = await self.thread.send(embed=game_info_embed)
            self.game_message = await self.thread.send(embed=getting_ready_embed)

        asyncio.create_task(send_game_messages())  # Nonblocking
        log.debug(
            f"Finished game setup for a new game in {setup_timer.stop()}ms."
            f" matchmaker ID: {self.status_message.id} game ID: {self.thread.id}")

    async def move_by_command(self, ctx: discord.Interaction, name: str, arguments: dict[str, typing.Any],
                              current_turn_required: bool = True) -> None:
        """
        Make a move by command. This function is called dynamically by handle_move in the main program.
        How it works:
        1. Call the game's move function
        2. Update the game message based on the changes to the move
        :param current_turn_required: whether the current turn is required for this command
        :param name: Name of movement function to call
        :param ctx: Discord context window
        :param arguments: the list of preparsed arguments to pass directly into the move function
        :return: None
        """
        log = self.logger.getChild("move[command]")
        if self.ending_game:  # Don't move if the game is ending
            log.warning(f"Denied interaction to command {name!r} with arguments {arguments!r}"
                        f" because the game is ending!"
                        f" context: {contextify(ctx)}")
            return

        async with self.processing_move:  # Get move processing lock
            log.debug(f"Now processing move command {name!r} with arguments {arguments!r} context: {contextify(ctx)}")
            self.current_turn = self.game.current_turn()
            if ctx.user.id != self.current_turn.id and current_turn_required:
                log.debug(f"current_turn_required command failed because it isn't this player's turn"
                          f" (should be {self.current_turn}) context: {contextify(ctx)}")
                message = await ctx.followup.send(content="It isn't your turn right now!", ephemeral=True)
                await message.delete(delay=5)
                return
            try:
                # Call the move function with arguments (player, <expanded arguments>
                callback = None
                all_move_commands = self.game.moves

                # Get the callback from the command
                for command in all_move_commands:
                    if command.name == name:
                        callback = command.callback
                        break

                # If it is None, attempt to call the command's name as an attribute
                if callback is None:
                    function_to_call = name
                else:  # Otherwise, use the callback value
                    function_to_call = callback

                move_response: Response = getattr(self.game, function_to_call)(
                    internal_player_to_player(db.database.get_player(ctx.user, ctx.guild.id), self.game_type),
                    **arguments)
            except Exception as e:
                log.error(f"Error {e!r} with command {name!r} with arguments {arguments!r}"
                          f" context: {contextify(ctx)}.")
                error_embed = ErrorEmbed(ctx, what_failed=f"Error occurred while making a move! ({type(e)})",
                                         reason=traceback.format_exc())
                await ctx.followup.send(embed=error_embed, ephemeral=True)
                return

            if move_response is not None:
                send_move, set_delete_hook = move_response.generate_message(ctx.followup.send, self.thread.id,
                                                                            enable_view_components=False)
                sent_message = await send_move
                if sent_message is False:  # This means there was a null Response, so delete
                    await ctx.delete_original_response()
                hook = set_delete_hook(sent_message)
                if hook:
                    await hook
            else:
                await ctx.delete_original_response()

            await self.display_game_state()  # Update game state

            if (outcome := self.game.outcome()) is not None:  # Game is over
                log.debug(f"Received not-null game outcome: {outcome!r}. Now ending game"
                          f" context: {contextify(ctx)}")
                message = await ctx.followup.send(content="Game over!", ephemeral=True)
                await message.delete(delay=5)
                await game_over(self, outcome)
                return

    async def move_by_button(self, ctx: discord.Interaction, name, arguments: dict[str, typing.Any],
                             current_turn_required: bool = True) -> None:
        """
        Callback for a move triggered by a button. This function is called dynamically by
        game_button_callback in the main program.
        :param ctx: discord Context for button interaction
        :param name: Name of game function to callback
        :param arguments: arguments to pass directly into the button function
        :param current_turn_required: whether the current turn is required for the button click
        :return: Nothing
        """
        log = self.logger.getChild("move[button]")
        if self.ending_game:  # Don't move if the game is ending
            log.warning(f"Denied interaction to command {name!r} with arguments {arguments!r}"
                        f" because the game is ending!"
                        f" context: {contextify(ctx)}")
            return

        async with self.processing_move:  # Get move processing lock
            log.debug(f"Now processing move command {name!r} with arguments {arguments!r} context: {contextify(ctx)}")
            # Update current turn
            self.current_turn = self.game.current_turn()

            # Check to make sure that it is current turn (if required)
            if ctx.user.id != self.current_turn.id and current_turn_required:
                log.debug(f"current_turn_required command failed because it isn't this player's turn"
                          f" (should be {self.current_turn.id} ({self.current_turn.name})) context: {contextify(ctx)}")
                message = await ctx.followup.send(content="It isn't your turn right now!", ephemeral=True)
                await message.delete(delay=5)
                return

            # Get callback
            callback_function = getattr(self.game, name)

            # Get signature
            signature = inspect.signature(callback_function).parameters

            # Convert str to int and float if required
            type_converted_arguments = {}
            for arg in arguments:
                argument_type = signature[arg].annotation
                if argument_type is int:
                    type_converted_arguments[arg] = int(arguments[arg])
                elif argument_type is float:
                    type_converted_arguments[arg] = float(arguments[arg])
                else:
                    type_converted_arguments[arg] = arguments[arg]

            # Call button's callback with player and converted arguments
            try:
                # Call the move function with arguments (player, <expanded arguments>)
                move_response: Response = callback_function(
                    internal_player_to_player(db.database.get_player(ctx.user, ctx.guild.id), self.game_type),
                    **type_converted_arguments)
            except Exception as e:
                log.error(f"Error {e!r} with command {name!r} with arguments {arguments!r}"
                          f" context: {contextify(ctx)}.")
                error_embed = ErrorEmbed(ctx, what_failed=f"Error occurred while making a move! ({type(e)})",
                                         reason=traceback.format_exc())
                await ctx.followup.send(embed=error_embed, ephemeral=True)
                return

            if move_response is not None:
                send_move, set_delete_hook = move_response.generate_message(ctx.followup.send, self.thread.id,
                                                                            enable_view_components=False)
                sent_message = await send_move
                hook = set_delete_hook(sent_message)
                if hook:
                    await hook

            # NOTE: move_by_button does not need the delete_original_response call because it doesn't show
            # Thinking...   This means we can effectively ignore a null response value

            # Update display painting
            await self.display_game_state()

            if (outcome := self.game.outcome()) is not None:  # Game is over
                log.debug(f"Received not-null game outcome: {outcome!r}. Now ending game"
                          f" context: {contextify(ctx)}")
                message = await ctx.followup.send(content="Game over!", ephemeral=True)
                await message.delete(delay=5)
                await game_over(self, outcome)
                return

    async def move_by_select(self, ctx: discord.Interaction, name: str, current_turn_required: bool = True):
        log = self.logger.getChild("move[select]")
        if self.ending_game:  # Don't move if the game is ending
            log.warning(f"Denied interaction to command {name!r}"
                        f" because the game is ending!"
                        f" context: {contextify(ctx)}")
            return

        async with self.processing_move:  # Get move processing lock
            log.debug(f"Now processing move command {name!r} context: {contextify(ctx)}")
            # Update current turn
            self.current_turn = self.game.current_turn()

            # Check to make sure that it is current turn (if required)
            if ctx.user.id != self.current_turn.id and current_turn_required:
                log.debug(f"current_turn_required command failed because it isn't this player's turn"
                          f" (should be {self.current_turn.id} ({self.current_turn.name})) context: {contextify(ctx)}")
                message = await ctx.followup.send(content="It isn't your turn right now!", ephemeral=True)
                await message.delete(delay=5)
                return

            # Get callback
            callback_function = getattr(self.game, name)

            # Call button's callback with player and converted arguments
            try:
                # Call the move function with arguments (player, values)
                move_response: Response = callback_function(
                    internal_player_to_player(db.database.get_player(ctx.user, ctx.guild.id), self.game_type),
                    ctx.data["values"])
            except Exception as e:
                log.error(f"Error {e!r} with command {name!r}"
                          f" context: {contextify(ctx)}.")
                error_embed = ErrorEmbed(ctx, what_failed=f"Error occurred while making a move! ({type(e)})",
                                         reason=traceback.format_exc())
                await ctx.followup.send(embed=error_embed, ephemeral=True)
                return

            if move_response is not None:
                send_move, set_delete_hook = move_response.generate_message(ctx.followup.send, self.thread.id,
                                                                            enable_view_components=False)
                sent_message = await send_move
                hook = set_delete_hook(sent_message)
                if hook:
                    await hook

            # NOTE: move_by_select does not need the delete_original_response call because it doesn't show
            # Thinking...   This means we can effectively ignore a null response value

            # Update display painting
            await self.display_game_state()

            if (outcome := self.game.outcome()) is not None:  # Game is over
                log.debug(f"Received not-null game outcome: {outcome!r}. Now ending game"
                          f" context: {contextify(ctx)}")
                message = await ctx.followup.send(content="Game over!", ephemeral=True)
                await message.delete(delay=5)
                await game_over(self, outcome)
                return

    async def display_game_state(self) -> None:
        """
        Use the Game class (self.game) to get an updated version of the game state.
        :return: None
        """
        log = self.logger.getChild("display_game_state")
        update_timer = Timer().start()
        self.current_turn = self.game.current_turn()
        # Embed to send as the updated game state
        info_embed = CustomEmbed(title=f"Playing {self.game.name} with {len(self.players)} players",
                                 description=textify(TEXTIFY_CURRENT_GAME_TURN,
                                                     {"player": self.current_turn.mention})).remove_footer()

        # Game state embed and view
        state_embed = CustomEmbed().remove_footer()
        state_view = discord.ui.View()
        should_use_embed = False
        should_use_view = False

        game_state = self.game.state()  # Get the game state from the game

        state_types = {}
        limits = {}
        picture = None

        removed_fields = 0

        if game_state is not None:
            for state_type in game_state:

                # Call transformation functions if they exist
                if hasattr(state_type, "_embed_transform"):
                    state_type._embed_transform(state_embed)
                    if len(state_embed.fields) >= 25:
                        for remove_index in range(25, len(state_embed.fields)):
                            state_embed.remove_field(remove_index)
                    should_use_embed = True
                    removed_fields += len(state_embed.fields) - 25
                if hasattr(state_type, "_view_transform"):
                    state_type._view_transform(state_view, self.thread.id)
                    should_use_view = True

                limits[state_type.type] = state_type.limit  # Add limit for the state type, regardless of if it exists

                # Keep track of the amount of each state type
                if state_type.type in state_types:
                    state_types[state_type.type] += 1
                else:
                    state_types[state_type.type] = 1

                if state_type.type == "image":  # Special flag to extract discord.File object from the image type
                    picture = state_type.game_picture
        else:
            state_embed.add_field(name=":cobweb: There's nothing here!", value="The game didn't return a value "
                                                                               "for the game state, so nothing is displayed.",
                                  inline=False)
            should_use_embed = True  # Force the embed to be sent

        # Detect if over limit
        if removed_fields > 0:
            log.warning(f"Had to discard {removed_fields} fields from processing due to being over the limit!"
                        f" This could cause a bad paint. game_id={self.thread.id} game_type={self.game_type}")

        for limit_type in limits:
            if state_types[limit_type] > limits[limit_type]:
                log.warning(f"Unsure method determined that state type {limit_type!r}"
                            f" was over the limit ({state_types[limit_type]} > {limits[limit_type]})"
                            f" game_id={self.thread.id} game_type={self.game_type}")

        # Add info embed data
        info_embed.add_field(name="Players:", value=column_names(self.players))
        info_embed.add_field(name="Ratings:", value=column_elo(self.players, self.game_type))
        info_embed.add_field(name="Turn:", value=column_turn(self.players, self.current_turn))

        # Edit the game and info messages with the new embeds
        async def edit_info_message():
            while self.info_message is None:
                await asyncio.sleep(1)
            await self.info_message.edit(embed=info_embed)

        asyncio.create_task(edit_info_message())

        if picture is not None:  # For some reason, [None] is not accepted by discord.py, so send it None if no image.
            attachments = [picture]
        else:
            attachments = []

        pass_data = {"attachments": attachments}

        if should_use_embed:
            pass_data["embed"] = state_embed
        else:
            pass_data["embed"] = None
        if should_use_view:
            pass_data["view"] = state_view
        else:
            pass_data["view"] = None

        async def edit_game_message():
            while self.game_message is None:
                await asyncio.sleep(1)
            await self.game_message.edit(**pass_data)

        asyncio.create_task(edit_game_message())

        # Edit overview embed with new data
        async def edit_status_message():
            while self.status_message is None:
                await asyncio.sleep(1)
            await self.status_message.edit(
                embed=GameOverviewEmbed(self.game.name, self.game_type, self.rated, self.players, self.current_turn))

        asyncio.create_task(edit_status_message())

        # async def purge_phantom():
        #     while self.game_message is None:
        #         await asyncio.sleep(1)
        #     await self.game_message.channel.purge(limit=100, check=lambda msg: not (msg.guild.me == msg.author))
        #
        # asyncio.create_task(purge_phantom())

        log.debug(f"Finished game state update task in {update_timer.stop()}ms."
                  f" game_id={self.thread.id} game_type={self.game_type}")

    async def bump(self):
        self.game_message = self.game_message.channel.send()


class MatchmakingInterface:
    """
    MatchmakingInterface - the class that handles matchmaking for a game, where control is promptly handed off to a GameInterface
    via the successful_matchmaking function.
    """

    def __init__(self, creator: discord.User, game_type: str, message: discord.InteractionMessage,
                 rated: bool, private: bool):

        # Whether the startup of the matchmaking interaction failed
        self.failed = None

        # Game type
        self.game_type = game_type

        # Creator of the game
        self.creator = creator

        # Is the game rated?
        self.rated = rated

        # Whether joining the game is open
        self.private = private

        # Allowed players for whitelist
        self.whitelist = {db.database.get_player(creator, message.guild.id)}

        # Disallowed players (blacklist
        self.blacklist = set()

        # Game module
        self.module = importlib.import_module(GAME_TYPES[game_type][0])

        # Start the list of queued players with just the creator
        self.queued_players = set(self.whitelist)

        # The message context to edit when making updates
        self.message = message

        if self.queued_players == {None}:  # Couldn't get information on the creator, so fail now
            fail_embed = ErrorEmbed(what_failed="Couldn't connect to database!",
                                    reason="The bot failed to connect to the database."
                                           " This is likely a temporary error, try again later!")
            self.failed = fail_embed
            return
        CURRENT_MATCHMAKING.update({self.message.id: self})
        IN_MATCHMAKING.update({p: self for p in self.queued_players})

        # Game class
        self.game = getattr(self.module, GAME_TYPES[game_type][1])

        # Required and maximum players for game TODO: more complex requirements for start/stop

        if not hasattr(self.game, "players"):  # If no players defined, any value is "fine"
            self.player_verification_function = lambda x: True
            self.allowed_players = "Any"
        else:
            self.player_verification_function = player_verification_function(self.game.players)
            self.allowed_players = player_representative(self.game.players)

        self.outcome = None  # Whether the matchmaking was successful (True, None, or False)
        self.logger = logging.getLogger(f"playcord.matchmaking_interface[{message.id}]")

    async def update_embed(self) -> None:
        """
        Update the embed based on the players in self.players
        :return: Nothing
        """
        log = self.logger.getChild("update_embed")
        update_timer = Timer().start()
        # Set up the embed

        game_rated_text = "Rated" if self.rated else "Not Rated"
        private_text = "🔐Private" if self.private else "🔓Public"

        # Parameters in embed title:
        # Time
        # Allowed players
        # Difficulty
        # Rated/Unrated
        # Public/Private

        game_metadata = {}

        for param in ["time", "difficulty", "author", "author_link", "source_link"]:
            if hasattr(self.game, param):
                game_metadata[param] = getattr(self.game, param)
            else:
                game_metadata[param] = "Unknown"

        embed = CustomEmbed(title=f"Queueing for {self.game.name}...",
                            description=f"⏰{game_metadata['time']}{LONG_SPACE_EMBED * 2}"
                                        f"👤{self.allowed_players}{LONG_SPACE_EMBED * 2}"
                                        f"📈{game_metadata['difficulty']}{LONG_SPACE_EMBED * 2}"
                                        f"📊{game_rated_text}{LONG_SPACE_EMBED * 2}"
                                        f"{private_text}")

        # Add columns for names, elo, and creator status
        embed.add_field(name="Players:", value=column_names(self.queued_players), inline=True)
        embed.add_field(name="Rating:", value=column_elo(self.queued_players, self.game_type), inline=True)
        embed.add_field(name="Creator:", value=column_creator(self.queued_players, self.creator), inline=True)

        # Add whitelist or blacklist depending on private status
        if self.private:
            embed.add_field(name="Whitelist:", value=column_names(self.whitelist), inline=True)
        elif len(self.blacklist):
            embed.add_field(name="Blacklist:", value=column_names(self.blacklist), inline=True)

        # Credits for game
        embed.add_field(name="Game Info:", value=self.game.description, inline=False)
        embed.add_field(name="Game by:",
                        value=f"[{game_metadata['author']}]({game_metadata['author_link']})\n[Source]({game_metadata['source_link']})")

        # Can the start button be pressed?
        start_enabled = self.player_verification_function(len(self.queued_players))

        # Create matchmaking button view (with callbacks and can_start)
        view = MatchmakingView(join_button_id=f"join/{self.message.id}",
                               leave_button_id=f"leave/{self.message.id}",
                               start_button_id=f"start/{self.message.id}",
                               can_start=start_enabled)

        # Update the embed in Discord
        await self.message.edit(embed=embed, view=view)
        log.debug(f"Finished matchmaking update task in {update_timer.stop()}ms.")

    async def accept_invite(self, ctx: discord.Interaction) -> bool:
        """
        Accept a invite.
        :param ctx: discord context with information about the invite
        :return: whether the invite succeeded or failed
        """

        player = get_shallow_player(ctx.user)

        # Get logger
        log = self.logger.getChild("accept_invite")
        log.debug(f"Attempting to accept invite for player {player} for matchmaker id={self.message.id}"
                  f" {contextify(ctx)}")

        if player.id in [p.id for p in self.queued_players]:  # Can't join if you are already in
            log.debug(
                f"Player.py {player} attempted to accept invite, but they are already in the game! "
                f"{contextify(ctx)}")
            await ctx.followup.send("You are already in the game!", ephemeral=True)
            return False
        else:
            if player is None:  # Couldn't retrieve information, so don't join them
                log.warning(
                    f"Player.py {player} attempted to accept invite, but we couldn't connect to the database!"
                    f"{contextify(ctx)}")
                await ctx.followup.send("Couldn't connect to DB!", ephemeral=True)
                return False

            # Add to whitelist or remove from blacklist, depending on private/public status
            if self.private:
                self.whitelist.add(player)
            else:
                try:
                    self.blacklist.remove(player)
                except KeyError:
                    pass

            self.queued_players.add(player)  # Add the player to queued_players
            IN_MATCHMAKING.update({player: self})
            log.debug(
                f"Successfully accepted invite for {player.id} ({player.name})!"
                f"{contextify(ctx)}")
            await self.update_embed()  # Update embed on discord side
        return True

    async def ban(self, player: discord.User, reason: str) -> str | None:
        """
        Ban a player from the game with reason
        :param player: the player to ban
        :param reason: the reason the player was banned
        :return: Error code or None if no error
        """
        log = self.logger.getChild("ban")
        new_player = db.database.get_player(player, self.message.guild.id)
        log.debug(f"Attempting to ban player {new_player} for reason {reason!r}...")
        if new_player is None:  # Couldn't retrieve information, so don't join them
            log.error(f"Error banning {new_player}: couldn't connect to the database!")
            return "Couldn't connect to DB!"

        # Kick if already in and update embed
        kicked = False
        if new_player.id in [p.id for p in self.queued_players]:
            kicked = True
            self.queued_players.remove(new_player)
            IN_MATCHMAKING.pop(new_player)

        # end game if necessary
        if not len(self.queued_players):
            await self.message.delete()  # Remove matchmaking message
            self.outcome = False
            log.info(f"Self ban of player {new_player} caused the lobby to end.")
            return "idk why you banned yourself when you are the only one in the lobby, lol"

        if player.id == self.creator.id:  # Update creator if the person leaving was the creator.
            self.creator = next(iter(self.queued_players)).user

        # If private game: remove from whitelist
        # If public game: add to blacklist
        if self.private:
            try:
                self.whitelist.remove(new_player)
            except KeyError:
                log.info(f"Ban of player {new_player} in private lobby failed: not on whitelist anyway.")
                return "Can't ban someone who isn't on the whitelist anyway!"
        else:
            self.blacklist.add(new_player)

        await self.update_embed()  # Update embed now that we have done all operations

        if kicked:
            log.info(f"Successfully kicked and banned {new_player}"
                     f" from the game for reason {reason!r}")
            return f"Successfully kicked and banned {player.mention} from the game for reason {reason!r}"
        log.info(f"Successfully banned {new_player}"
                 f" from the game for reason {reason!r}")
        return f"Successfully banned {player.mention} from the game for reason {reason!r}"

    async def kick(self, player: discord.User, reason: str) -> str | None:
        """
        Kick a player from the game with reason
        :param player: the player to kick
        :param reason: reason the player was kicked
        :return: error or None if no error
        """
        log = self.logger.getChild("kick")
        new_player = get_shallow_player(player)
        log.debug(f"Attempting to kick player {new_player} for reason {reason!r}...")
        if new_player is None:  # Couldn't retrieve information, so don't join them
            log.error(f"Error kicking {new_player}: couldn't connect to the database!")
            return "Couldn't connect to DB!"

        kicked = False
        if new_player.id in [p.id for p in self.queued_players]:  # Kick if already in
            kicked = True
            self.queued_players.remove(new_player)
            IN_MATCHMAKING.pop(new_player)
            await self.update_embed()

        # end game if necessary
        if not len(self.queued_players):
            await self.message.delete()  # Remove matchmaking message
            self.outcome = False
            log.info(f"Self kick of player {new_player} caused the lobby to end.")
            return ("idk why you thought kicking yourself was a smart idea "
                    "when you are the only one in the lobby, lol")

        if player.id == self.creator.id:  # Update creator if the person leaving was the creator.
            self.creator = next(iter(self.queued_players)).user

        if kicked:
            log.info(f"Successfully kicked {new_player} ({player.name})"
                     f" from the game for reason {reason!r}")
            return f"Successfully kicked {player.mention} from the game for reason {reason!r}"
        log.info(f"Couldn't kick {new_player}"
                 f" from the game: they weren't in the lobby!")
        return f"Didn't kick anyone: {player.mention} isn't in this lobby!"

    async def callback_ready_game(self, ctx: discord.Interaction) -> None:
        """
        Callback for the selected player to join the game
        :param ctx: discord context
        :return: Nothing
        """
        log = self.logger.getChild("ready_game")
        new_player = get_shallow_player(ctx.user)
        log.debug(f"Attempting to join the game... {contextify(ctx)}")
        if ctx.user.id in [p.id for p in self.queued_players]:  # Can't join if you are already in
            log.info(f"Attempted to join player {new_player} but failed because they were already in the queue."
                     f" {contextify(ctx)}")
            await ctx.followup.send("You are already in the game!", ephemeral=True)
        else:
            if not self.private:
                if new_player in self.blacklist:
                    log.info(f"Attempted to join player {new_player} but failed because they were already in the queue."
                             f" {contextify(ctx)}")
                    await ctx.followup.send(f"You are banned from this game!"
                                            f" Ask the owner of the game {self.creator.mention}"
                                            f" to unban you!", ephemeral=True)
                    return
                self.queued_players.add(new_player)  # Add the player to queued_players
                IN_MATCHMAKING.update({new_player: self})
                await self.update_embed()  # Update embed on discord side
            else:
                if new_player not in self.whitelist:
                    log.info(f"Attempted to join player {new_player} to private game but failed because"
                             f" they were not on the whitelist."
                             f" {contextify(ctx)}")
                    await ctx.followup.send("You are not on the whitelist for this private game!", ephemeral=True)
                    return
                self.queued_players.add(new_player)  # Add the player to queued_players
                IN_MATCHMAKING.update({new_player: self})
                await self.update_embed()  # Update embed on discord side

    async def callback_leave_game(self, ctx: discord.Interaction) -> None:
        """
        Callback for the selected player to leave the matchmaking session
        :param ctx: discord context
        :return: None
        """
        log = self.logger.getChild("leave_game")
        log.debug(f"Attempting to leave the game... {contextify(ctx)}")
        player = get_shallow_player(ctx.user)

        if player.id not in [p.id for p in self.queued_players]:  # Can't leave if you weren't even there
            log.info(f"Attempted to remove player {player} but failed because they weren't in the queue to begin with."
                     f" {contextify(ctx)}")
            await ctx.followup.send("You aren't in the game!", ephemeral=True)
        else:
            # Remove player from queue
            for p in self.queued_players:
                if p.id == player.id:
                    self.queued_players.remove(player)
                    IN_MATCHMAKING.pop(player)
                    break
            # Nobody is left lol
            if not len(self.queued_players):
                log.info(f"Call to leave_game left no players in lobby, so ending game. {contextify(ctx)}")
                await ctx.followup.send("You were the last person in the lobby, so the game was cancelled!",
                                        ephemeral=True)
                await self.message.delete()  # Remove matchmaking message
                self.outcome = False
                return

            if player.id == self.creator.id:  # Update creator if the person leaving was the creator.
                new_creator = next(iter(self.queued_players))
                self.creator = new_creator.user
                log.debug(f"Successful leave_game call did not end the game,"
                          f" but we are removing the creator {player} from the game."
                          f" Selecting new creator {new_creator}. {contextify(ctx)}")

            await self.update_embed()  # Update embed again
        return

    async def callback_start_game(self, ctx: discord.Interaction) -> None:
        """
        Callback for the selected player to start the game.
        :param ctx: Discord context
        :return: Nothing
        """
        log = self.logger.getChild("start_game")
        player = get_shallow_player(ctx.user)
        log.debug(f"Attempting to start the game... {contextify(ctx)}")

        if ctx.user.id != self.creator.id:  # Don't have permissions to start the game
            await ctx.followup.send("You can't start the game (not the creator).", ephemeral=True)
            log.debug(f"Game failed to start because player {player} was not the creator. "
                      f"{contextify(ctx)}")
            return

        # The matchmaking was successful!
        self.outcome = True

        log.debug(f"Game successfully started by {player}!"
                  f"{contextify(ctx)}")
        # Start the GameInterface

        await self.message.edit(embed=CustomEmbed(description=get_emoji_string("loading")).remove_footer(),
                                view=None)
        await successful_matchmaking(interface=self)


async def successful_matchmaking(interface: MatchmakingInterface) -> None:
    """
    Callback called by MatchmakingInterface when the game is successfully started
    Sets up and registers a new GameInterface.
    :param interface: MatchmakingInterface that will be registered as a GameInterface by this function
    :return: Nothing
    """

    # Extract class variables
    game_class = interface.game
    rated = interface.rated
    players = interface.queued_players
    message = interface.message
    game_type = interface.game_type
    creator = interface.creator

    # Remove players in this matchmaking from IN_MATCHMAKING
    for p in players:
        IN_MATCHMAKING.pop(p)

    CURRENT_MATCHMAKING.pop(message.id)  # Remove the MatchmakingInterface from the CURRENT_MATCHMAKING tracker

    # Set up a new GameInterface
    new_game_id = db.database.create_game(game_name=game_type, guild_id=message.guild.id,
                                          participants=[player.id for player in players],
                                          is_rated=rated)
    game = GameInterface(game_type, message, creator, list(players), rated, new_game_id)
    await game.setup()  # Setup thread and other stuff

    # Register the game to the channel it's in
    CURRENT_GAMES.update({game.thread.id: game})

    # Edit the status message with the SpectateView
    async def create_spectate_view():
        while game.game_message is None:
            await asyncio.sleep(1)
        await message.edit(view=SpectateView(spectate_button_id=f"spectate/{game.thread.id}",
                                             peek_button_id=f"peek/{game.thread.id}/{game.game_message.id}",
                                             game_link=game.info_message.jump_url))

    asyncio.create_task(create_spectate_view())
    await game.display_game_state()  # Send the game display state


async def rating_groups_to_string(rankings: list[int], groups: list[dict[InternalPlayer, trueskill.Rating]],
                                  game_type: str) \
        -> tuple[str, dict[int, dict[str, str | bool | int | Any]]]:
    """
    Converts the rankings and groups from a rated game into a string representing the outcome of the game.
    :param rankings: Rankings (format: list of places such as [1, 1, 2, 3] to correlate with groups)
    :param groups: groups (format: [{player: player_rating}] where player is a Player.py object and player_rating
     is an trueskill.Rating object
    :param game_type: The game type, used to extract the correct rating from the player.
    :return: String representing the outcome of the game (format:
    1. PlayerInFirst
    2T. PlayerInSecond
    2T. PlayerAlsoInSecond
    4. LastPlayer
    )
    """

    # Dictionary containing all data relevant to the ratings
    player_ratings = {}

    # Place tracking variables
    current_place = 1
    nums_current_place = 0
    matching = 0

    # Turn list of dictionaries into a list of all the keys from the dictionaries
    keys = [next(iter(p)) for p in groups]

    # Convert the list of dictionaries into one dictionary with all of the keys
    all_ratings = {list(p.keys())[0]: list(p.values())[0] for p in groups}

    for i, pre_rated_player in enumerate(keys):  # Loop

        # Logic for keeping track of place
        if rankings[i] == matching:  # Same place ID as last person
            # Update the number of people who got the current place ID
            nums_current_place += 1
        else:  # Different ID, update to new ID and reset nums_current_place
            current_place += nums_current_place
            matching = rankings[i]
            nums_current_place = 1

        # Extract starting and ending rating variables
        starting_mu, starting_sigma = (getattr(pre_rated_player, game_type).mu,
                                       getattr(pre_rated_player, game_type).sigma)
        aftermath_mu, aftermath_sigma = all_ratings[pre_rated_player].mu, all_ratings[pre_rated_player].sigma

        # Change in ELO
        mu_delta = str(round(aftermath_mu - starting_mu))

        if not mu_delta.startswith("-"):  # Add a "+" to the delta if it isn't negative
            mu_delta = "+" + mu_delta

        # Add data for the player to player_ratings
        player_ratings.update({pre_rated_player.id: {"old_mu": round(starting_mu), "delta": mu_delta,
                                                     "place": current_place, "tied": rankings.count(rankings[i]) > 1,
                                                     "new_mu": aftermath_mu,
                                                     "old_sigma": starting_sigma,
                                                     "new_sigma": aftermath_sigma}})
    # Concatenate to
    # 1. PlayerOne 1 (+384)
    # 2. PlayerTwo 30 (+2)
    # 3. PlayerThreeWhoSucks 20 (-20)
    player_string = "\n".join([
        f"{player_ratings[p]["place"]}{"T" if player_ratings[p]["tied"] else ""}."
        f"{LONG_SPACE_EMBED}<@{p}>{LONG_SPACE_EMBED}{player_ratings[p]["old_mu"]}"
        f"{LONG_SPACE_EMBED}({player_ratings[p]["delta"]})"
        for p in player_ratings])

    # Return both the concatenated string AND the rating dictionary, as it is needed for the game_over function
    return player_string, player_ratings


async def non_rated_groups_to_string(rankings: list[int], groups: list[InternalPlayer]) -> str:
    """
    Create the string representing the groups for the game over screen
    :param rankings: Rankings: format [0, 1, 1, 2] for first place, two tied for 2nd, and 3rd. Corresponds to groups
    :param groups: Groups: format [list of players], places correspond to places in rankings
    :return: string of the groups formatted to display
    """

    # Output list to concatenate
    player_ratings = []

    # Loop variables
    current_place = 1
    nums_current_place = 0
    matching = 0

    # Loop through players
    for i, pre_rated_player in enumerate(groups):
        # Ranking of current player = last player ranked, so increment the number of people
        if rankings[i] == matching:
            nums_current_place += 1
        # New ranking
        else:
            current_place += nums_current_place  # Add number of people who were in previous ranking position
            matching = rankings[i]  # Now matching current player's ranking ID
            nums_current_place = 1

        # Check if tied
        show_tied = ""
        if rankings.count(rankings[i]) > 1:  # More than one player tied for same score
            show_tied = 'T'  # Display "T" (1T.)

        # Add format PLACE[TIED].   MENTION
        player_ratings.append(f"{current_place}{show_tied}.{LONG_SPACE_EMBED}{pre_rated_player.mention}")
    return "\n".join(player_ratings)  # concatenate and return


async def game_over(interface: GameInterface, outcome: str | InternalPlayer | list[list[InternalPlayer]]) -> None:
    """
    Callback called by GameInterface when the game is over. Easily the most technically complicated function
    in the entire API

    :param interface: GameInterface that the game_over was triggered by
    :param outcome: outcome of the game. There are three possibilities: string for error,
     one player who won (all other players lost), or a list of list of players formatted like this:
     [[p1, p2], [p3], [p4]] indicates p1 and p2 tied, p3 got third, p4 got fourth
    :return: Nothing
    """

    # Extract class variables
    interface.ending_game = True  # Prevent moves from being attempted after game is over
    game_type = interface.game_type
    thread = interface.thread
    outbound_message = interface.status_message
    rated = interface.rated
    players = interface.players
    game_id = interface.game_id

    # Get environment constants
    sigma = MU * GAME_TRUESKILL[game_type]["sigma"]
    beta = MU * GAME_TRUESKILL[game_type]["beta"]
    tau = MU * GAME_TRUESKILL[game_type]["tau"]
    draw = GAME_TRUESKILL[game_type]["draw"]

    # mpmath backend = near infinite floating point precision
    environment = trueskill.TrueSkill(mu=MU, sigma=sigma, beta=beta, tau=tau, draw_probability=draw,
                                      backend="mpmath")

    # There are three cases: str (error) Player (one person won) list[list[Player]] (detailed ranking)
    if isinstance(outcome, str):  # Error
        game_over_embed = ErrorEmbed(what_failed="Error during a move!", reason=outcome)
        # Send the embed
        await outbound_message.edit(embed=game_over_embed)
        await thread.edit(locked=True, archived=True, reason="Game crashed.")
        await thread.send(embed=game_over_embed)
        return

    if rated:
        if isinstance(outcome, Player):  # Somebody won, everybody else lost. No way of comparison (tic-tac-toe)
            # Winner's rating
            winner = environment.create_rating(outcome.mu, outcome.sigma)

            # All the losers
            losers = [{p: environment.create_rating(getattr(p, game_type).mu, getattr(p, game_type).sigma)}
                      for p in players if p != outcome]

            rating_groups = [{InternalPlayer(ratings={game_type: {"mu": outcome.mu, "sigma": outcome.sigma}},
                                             user=None, metadata={}, id=outcome.id): winner},
                             *losers]  # Make the rating groups, cast Player to InternalPlayer
            rankings = [0, *[1 for _ in range(len(players) - 1)]]  # Rankings = [0, 1, 1, ..., 1] for this case

        else:  # More generic position placement
            # Format:
            # [[p1, p2], [p3], [p4]] indicates p1 and p2 tied, p3 got third, p4 got fourth
            # What if there are teams? screw you

            current_ranking = 0
            rankings = []
            rating_groups = []
            for placement in outcome:
                for player in placement:
                    rankings.append(current_ranking)
                    rating_groups.append({player: environment.create_rating(player.mu, player.sigma)})
                current_ranking += 1

        # Rerate the groups
        adjusted_rating_groups = environment.rate(rating_groups=rating_groups, ranks=rankings)
        player_string, player_ratings = await rating_groups_to_string(rankings, adjusted_rating_groups, game_type)
        print(rankings, rating_groups, adjusted_rating_groups, player_string, player_ratings)
        ratings = {}
        for player in player_ratings:
            data = player_ratings[player]
            new_mu = data["new_mu"]
            new_sigma = data["new_sigma"]
            ratings.update({player: {"uid": player,
                                     "new_mu": new_mu,
                                     "new_sigma": new_sigma,
                                     "mu_delta": new_mu - data["old_mu"],
                                     "sigma_delta": new_sigma - data["old_sigma"], "ranking": 3}})

        db.database.end_game(match_id=game_id, game_name=game_type, rating_updates=ratings, final_scores=None)


    else:  # Non-rated game

        # In case of impossible fail: no rankings
        rankings = []
        groups = []

        if isinstance(outcome, Player):
            groups = [outcome, *[p for p in players if p != outcome]]  # Make the rating groups
            rankings = [0, *[1 for _ in range(len(players) - 1)]]  # Rankings = [0, 1, 1, ..., 1] for this case

        elif isinstance(outcome, list):
            current_ranking = 0
            rankings = []
            groups = []
            for placement in outcome:
                for player in placement:
                    rankings.append(current_ranking)
                    groups.append(player)
                current_ranking += 1

        player_string = await non_rated_groups_to_string(rankings, groups)

    for p in players:  # Players playing this game are no longer in the game... it's over lol
        IN_GAME.pop(p)

    CURRENT_GAMES.pop(thread.id)  # Remove this game from the CURRENT_GAMES tracker

    # Create GameOverEmbed to show in the status and info messages
    game_over_embed = GameOverEmbed(rankings=player_string)

    # Send the embed to overview / game thread
    await thread.send(embed=game_over_embed)
    await outbound_message.edit(embed=game_over_embed, view=None)

    # Close the game thread
    await thread.edit(locked=True, archived=True, reason="Game is over.")

    # # If the game is rated, perform the relatively intensive task of updating the DB rankings
    # if rated:
    #     for player_id in player_ratings:  # Every rated player, post new ratings in the database
    #         player_data = player_ratings[player_id]
    #         update_player(game_type, InternalPlayer(mu=player_data["new_mu"],
    #                                                 sigma=player_data["new_sigma"],
    #                                                 user=discord.Object(id=player_id)))
    #
    #     update_db_rankings(game_type)  # Update ranking db variable
