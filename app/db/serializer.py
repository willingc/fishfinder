"""Type-safe JSON serialization for complex PostgreSQL values."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
from typing import Any
from uuid import UUID


def format_interval(delta: timedelta) -> str:
    """Format a Python timedelta (PostgreSQL INTERVAL) into a clean human-readable string."""
    total_seconds = int(delta.total_seconds())
    sign = "-" if total_seconds < 0 else ""
    remaining = abs(total_seconds)

    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return f"{sign}{' '.join(parts)}"


def _is_range_like(value: Any) -> bool:
    """Detect psycopg Range objects or duck-typed range values."""
    return all(
        hasattr(value, attr)
        for attr in ("lower", "upper", "lower_inc", "upper_inc", "isempty")
    )


def format_range(value: Any) -> str:
    """Format PostgreSQL range types (daterange, int4range, tstzrange, etc.)."""
    if value.isempty:
        return "empty"
    left_bracket = "[" if value.lower_inc else "("
    right_bracket = "]" if value.upper_inc else ")"
    lower_str = "" if value.lower is None else str(serialize_value(value.lower))
    upper_str = "" if value.upper is None else str(serialize_value(value.upper))
    return f"{left_bracket}{lower_str}, {upper_str}{right_bracket}"


def serialize_value(value: Any, *, max_bytes_hex: int = 64) -> Any:
    """Convert a PostgreSQL Python value into a JSON-safe representation."""
    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value

    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return str(value)
        # Preserve clean numeric values for JSON / UI sorting while avoiding float noise
        return float(value)

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, timedelta):
        return format_interval(value)

    if isinstance(
        value,
        (IPv4Address, IPv6Address, IPv4Network, IPv6Network, IPv4Interface, IPv6Interface),
    ):
        return str(value)

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        hex_str = raw[:max_bytes_hex].hex()
        suffix = f"... (+{len(raw) - max_bytes_hex} bytes)" if len(raw) > max_bytes_hex else ""
        return f"\\x{hex_str}{suffix}"

    if _is_range_like(value):
        return format_range(value)

    if isinstance(value, (list, tuple)):
        return [serialize_value(item, max_bytes_hex=max_bytes_hex) for item in value]

    if isinstance(value, dict):
        return {
            str(k): serialize_value(v, max_bytes_hex=max_bytes_hex)
            for k, v in value.items()
        }

    return str(value)


def serialize_rows(columns: list[str], rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Serialize database rows into a list of column-keyed JSON-safe dictionaries."""
    return [
        {col: serialize_value(val) for col, val in zip(columns, row)}
        for row in rows
    ]
