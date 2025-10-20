import asyncio
import logging
from functools import wraps
from telegram.error import RetryAfter

logger = logging.getLogger(__name__)

def rate_limit_handler(func):
    """
    A decorator to handle Telegram API rate limits gracefully.

    Catches `telegram.error.RetryAfter` and waits for the specified
    duration before retrying the decorated function.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            logger.warning(f"Rate limit exceeded. Retrying in {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await func(*args, **kwargs)
    return wrapper