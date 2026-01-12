class Player:
    """
    Represents a player in a game.

    This class is used both for in-game player representation and for
    rating/ranking purposes with TrueSkill.
    """

    # TrueSkill default values
    DEFAULT_MU = 1000
    DEFAULT_SIGMA_RATIO = 1 / 6  # sigma = mu * ratio

    def __init__(self, mu: float = None, sigma: float = None,
                 ranking: int = None, id: int = None, name: str = None):
        """
        Create a new Player.

        :param mu: TrueSkill mu value (skill estimate)
        :param sigma: TrueSkill sigma value (uncertainty)
        :param ranking: The player's ranking in the current game
        :param id: Discord user ID
        :param name: Player's display name
        """
        self.mu = mu if mu is not None else self.DEFAULT_MU
        self.sigma = sigma if sigma is not None else self.mu * self.DEFAULT_SIGMA_RATIO
        self.id = id
        self.name = name
        self.player_data = {}  # Arbitrary game-specific data
        self.ranking = ranking

    @property
    def mention(self) -> str:
        """Get the Discord mention string for this player."""
        return f"<@{self.id}>"  # Don't use the potential self.user.mention because it could be an Object

    @property
    def conservative_rating(self) -> float:
        """
        Get the conservative skill estimate (mu - 3*sigma).
        This is the TrueSkill conservative rating used for leaderboards.
        """
        return self.mu - 3 * self.sigma

    @property
    def display_rating(self) -> int:
        """Get the rounded mu value for display."""
        return round(self.mu)

    def get_formatted_elo(self, uncertainty_threshold: float = 0.20) -> str:
        """
        Get a formatted ELO string with uncertainty indicator.

        :param uncertainty_threshold: If sigma/mu > threshold, add '?' to indicate uncertainty
        :return: Formatted rating string like "1000" or "1000?"
        """
        if self.mu is None:
            return "No Rating"

        # Check if the rating is uncertain (high sigma relative to mu)
        if self.sigma > uncertainty_threshold * self.mu:
            return f"{round(self.mu)}?"

        return str(round(self.mu))

    def __eq__(self, other) -> bool:
        if other is None:
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __str__(self) -> str:
        return self.mention

    def __repr__(self) -> str:
        return f"Player(id={self.id}, mu={self.mu}, sigma={self.sigma})"
