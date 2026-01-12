from api.Arguments import String
from api.Command import Command
from api.Game import Game
from api.MessageComponents import Button, ButtonStyle, DataTable


class TicTacToeGame(Game):
    begin_command_description = "The classic game of Xs and Os, brought to discord"
    move_command_group_description = "Commands for TicTacToe"
    description = ("Tic-Tac-Toe on Discord! The game is pretty self-explanatory,"
                   " just take turns placing Xs and Os until one player gets three in a row!")
    name = "Tic-Tac-Toe"
    players = 2
    moves = [Command(name="move", description="Place a piece down.",
                     options=[String(argument_name="move", description="description", autocomplete="ac_move")])]
    author = "@quantumbagel"
    version = "1.0"
    author_link = "https://github.com/quantumbagel"
    source_link = "https://github.com/PlayCord/bot/blob/main/games/TicTacToeV2.py"
    time = "2min"
    difficulty = "Literally Braindead"

    def __init__(self, players):

        # Initial state information
        self.players = players
        self.x = self.players[0]
        self.o = self.players[1]
        self.size = 3

        # Dynamically updated information
        self.board = [[BoardCell() for _ in range(self.size)] for _ in range(self.size)]
        self.turn = 0
        self.row_count = [0 for _ in range(self.size)]
        self.column_count = [0 for _ in range(self.size)]
        self.diagonal_count = 0
        self.anti_diagonal_count = 0

    def state(self):
        buttons = []
        for col in range(3):
            for row in range(3):
                name = None
                emoji = None
                if self.board[row][col].id == self.x.id:
                    emoji = "❌"
                elif self.board[row][col].id == self.o.id:
                    emoji = "⭕"

                if emoji == "❌":
                    color = ButtonStyle.blurple
                elif emoji == "⭕":
                    color = ButtonStyle.green
                else:
                    color = ButtonStyle.gray

                button = Button(label=name, emoji=emoji, callback=self.move, row=row, style=color,
                                arguments={"move": str(col) + str(row)})
                buttons.append(button)
        return_this = [DataTable({self.x: {"Team:": ":x:"}, self.o: {"Team:": ":o:"}})]
        return_this.extend(buttons)

        return return_this

    def current_turn(self):
        return self.players[self.turn]

    def ac_move(self, player):
        moves = []
        all_moves = {'00': 'Top Left', '01': 'Top Mid', '02': 'Top Right', '10': 'Mid Left', '11': 'Mid Mid',
                     '12': 'Mid Right', '20': 'Bottom Left', '21': 'Bottom Mid', '22': 'Bottom Right'}
        for row in range(self.size):
            for column in range(self.size):
                if self.board[row][column].id is None:
                    move_id = str(row) + str(column)
                    moves.append({all_moves[move_id]: move_id})
        return moves

    def move(self, player, move):
        self.board[int(move[1])][int(move[0])].take(self.players[self.turn])
        self.turn += 1
        if self.turn == len(self.players):
            self.turn = 0

    def outcome(self):
        # Check rows
        for row in self.board:
            if row[0].id is not None and all(cell.id == row[0].id for cell in row):
                return row[0].owner  # Return the winner's owner

        # Check columns
        for col in range(3):
            if (self.board[0][col].id is not None and
                    all(self.board[row][col].id == self.board[0][col].id for row in range(3))):
                return self.board[0][col].owner  # Return the winner's owner

        # Check diagonals
        if self.board[0][0].id is not None and all(self.board[i][i].id == self.board[0][0].id for i in range(3)):
            return self.board[0][0].owner  # Return the winner's owner
        if self.board[0][2].id is not None and all(self.board[i][2 - i].id == self.board[0][2].id for i in range(3)):
            return self.board[0][2].owner  # Return the winner's owner

        # Check for a draw (self.board is full and no winner)
        if all(cell.id is not None for row in self.board for cell in row):
            # Collect all unique IDs from the self.board
            ids = [[self.players[0], self.players[1]]]
            return ids  # Return list of both IDs


class BoardCell:
    """Represents a cell on the game board that can be owned by a player."""

    def __init__(self, player=None):
        if player is not None:
            self.id = player.id
        else:
            self.id = None
        self.owner = player

    def take(self, player):
        self.id = player.id
        self.owner = player

    def __repr__(self):
        return f"BoardCell(id={self.id})"

    def __eq__(self, other):
        if other is None:
            return False
        return self.id == other.id
