"""Standard API helpers for TopstepX backend services.

Centralized utilities for HTTP headers and timezone-aware datetime operations.
Avoids header drift across services and ensures consistent UTC timestamps.
"""

from datetime import datetime, timezone
from typing import Dict


def auth_headers(token: str) -> Dict[str, str]:
    """
    Get standardized authentication headers for TopstepX API calls.
    
    Based on ProjectX Gateway API documentation, uses 'accept: text/plain' 
    as shown in all API examples.

    Args:
        token: JWT authentication token

    Returns:
        Dictionary with Content-Type, Accept, and Authorization headers
    """
    return {
        "Content-Type": "application/json",
        "Accept": "text/plain",
        "Authorization": f"Bearer {token}",
    }


def utc_now() -> datetime:
    """
    Get current UTC datetime with timezone awareness.

    Returns:
        Timezone-aware datetime in UTC
    """
    return datetime.now(timezone.utc)


def utc_timestamp() -> int:
    """
    Get current UTC timestamp in milliseconds.

    Returns:
        Current time as milliseconds since epoch
    """
    return int(utc_now().timestamp() * 1000)


def format_iso_utc(dt: datetime) -> str:
    """
    Format datetime as ISO string in UTC timezone.

    Args:
        dt: Datetime to format (will be converted to UTC if not already)

    Returns:
        ISO formatted string with UTC timezone
    """
    if dt.tzinfo is None:
        # Assume naive datetime is already UTC
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        # Convert to UTC if in different timezone
        dt = dt.astimezone(timezone.utc)

    return dt.isoformat().replace("+00:00", "Z")
