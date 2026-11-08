"""Tools used when toolregistry-hub is not installed."""

from datetime import datetime, timezone


def echo(text: str) -> str:
    """Return the text unchanged.

    Args:
        text: The text to echo.

    Returns:
        The same text.
    """
    return text


def add(a: float, b: float) -> float:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        The sum of a and b.
    """
    return a + b


def server_time() -> str:
    """Return the server's current UTC time.

    Returns:
        An ISO 8601 timestamp.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
