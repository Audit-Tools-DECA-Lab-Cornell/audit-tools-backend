"""
Shared SlowAPI limiter instance for rate-limited HTTP routes.

The application sets ``app.state.limiter`` to this object so decorators on
routers resolve the same limiter at request time.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
