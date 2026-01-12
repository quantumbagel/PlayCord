"""
Emoji utilities

Provides functionality for:
- Loading emojis from configuration
- Registering custom emojis at runtime
- Getting emoji strings for use in Discord messages
"""
import logging
from typing import Optional

import ruamel.yaml

from configuration.constants import EMOJI_CONFIGURATION_FILE, LONG_SPACE_EMBED

logger = logging.getLogger("playcord.emojis")

# Loaded emojis from configuration
emojis: dict[str, dict] = {}

# Runtime-registered emojis (not persisted)
runtime_emojis: dict[str, dict] = {}

initialized = False


def initialize_emojis() -> bool:
    """
    Initialize emojis from the configuration file.

    :return: True if successful, False otherwise
    """
    global emojis, initialized
    initialized = True
    try:
        emojis = ruamel.yaml.YAML().load(open(EMOJI_CONFIGURATION_FILE))["emojis"]
        logger.info(f"Loaded {len(emojis)} emojis from configuration.")
        return True
    except FileNotFoundError:
        logger.critical(f"Emoji configuration file not found: {EMOJI_CONFIGURATION_FILE}")
        return False
    except Exception as e:
        logger.critical(f"Failed to load emoji configuration file: {e}")
        return False


def register_emoji(name: str, emoji_id: int, animated: bool = False) -> bool:
    """
    Register a custom emoji at runtime.

    :param name: The name/key for the emoji
    :param emoji_id: The Discord emoji ID
    :param animated: Whether the emoji is animated
    :return: True if registered successfully
    """
    if name in emojis or name in runtime_emojis:
        logger.warning(f"Emoji {name!r} already exists. Overwriting.")

    runtime_emojis[name] = {
        "id": emoji_id,
        "animated": animated
    }
    logger.debug(f"Registered runtime emoji: {name} (id={emoji_id}, animated={animated})")
    return True


def unregister_emoji(name: str) -> bool:
    """
    Unregister a runtime emoji.

    :param name: The name/key of the emoji to unregister
    :return: True if unregistered, False if not found
    """
    if name in runtime_emojis:
        del runtime_emojis[name]
        logger.debug(f"Unregistered runtime emoji: {name}")
        return True
    return False


def get_emoji(name: str) -> Optional[dict]:
    """
    Get emoji data by name.

    :param name: The name/key of the emoji
    :return: Emoji data dict or None if not found
    """
    if not initialized:
        initialize_emojis()

    # Check runtime emojis first (they can override config emojis)
    if name in runtime_emojis:
        return runtime_emojis[name]

    if name in emojis:
        return emojis[name]

    return None


def get_emoji_string(name: str) -> str:
    """
    Get a formatted emoji string for use in Discord messages.

    :param name: The name/key of the emoji
    :return: Formatted emoji string like <:name:id> or <a:name:id> for animated
    """
    if not initialized:
        initialize_emojis()

    # Check runtime emojis first
    emoji = get_emoji(name)

    if emoji is None:
        if emojis:  # Only warn if we actually have emojis loaded
            logger.warning(f"Emoji {name!r} not found in configuration file. This emoji will not be used"
                           f" and a long space will fill its place.")
        return LONG_SPACE_EMBED

    if emoji.get("animated", False):
        return f"<a:{name}:{emoji['id']}>"
    else:
        return f"<:{name}:{emoji['id']}>"


def get_all_emojis() -> dict[str, dict]:
    """
    Get all registered emojis (config + runtime).

    :return: Dictionary of all emoji names to their data
    """
    if not initialized:
        initialize_emojis()

    # Merge with runtime emojis taking precedence
    all_emojis = {**emojis, **runtime_emojis}
    return all_emojis


def get_emoji_count() -> tuple[int, int]:
    """
    Get the count of loaded emojis.

    :return: Tuple of (config emoji count, runtime emoji count)
    """
    return len(emojis), len(runtime_emojis)
