"""Unit tests for complex PostgreSQL type serialization."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network, IPv6Address
from uuid import UUID

from psycopg.types.range import Range

from app.db.serializer import serialize_rows, serialize_value


def test_serializes_complex_postgres_types() -> None:
    sample_uuid = UUID("10000000-0000-4000-8000-000000000001")
    sample_dt = datetime(2026, 11, 19, 13, 30, 0, tzinfo=timezone.utc)
    sample_range = Range(date(2026, 1, 1), date(2027, 1, 1), "[)")

    assert serialize_value(Decimal("12345.67")) == 12345.67
    assert serialize_value(sample_uuid) == "10000000-0000-4000-8000-000000000001"
    assert serialize_value(sample_dt) == "2026-11-19T13:30:00+00:00"
    assert serialize_value(date(2026, 11, 19)) == "2026-11-19"
    assert serialize_value(time(9, 15, 30)) == "09:15:30"
    assert serialize_value(timedelta(hours=2, minutes=45)) == "2h 45m"
    assert serialize_value(timedelta(days=14)) == "14d"
    assert serialize_value(IPv4Address("198.51.100.14")) == "198.51.100.14"
    assert serialize_value(IPv6Address("2001:db8::1")) == "2001:db8::1"
    assert serialize_value(IPv4Network("10.0.0.0/24")) == "10.0.0.0/24"
    assert serialize_value(b"\xde\xad\xbe\xef") == "\\xdeadbeef"
    assert serialize_value(memoryview(b"\x01\x02")) == "\\x0102"
    assert serialize_value(sample_range) == "[2026-01-01, 2027-01-01)"
    assert serialize_value(Range(empty=True)) == "empty"


def test_serializes_nested_jsonb_and_arrays() -> None:
    payload = {
        "amount": Decimal("99.50"),
        "tags": ["vip", "beta_tester"],
        "nested": {"id": UUID("20000000-0000-4000-8000-000000000001"), "ip": IPv4Address("127.0.0.1")},
    }
    serialized = serialize_value(payload)
    assert serialized == {
        "amount": 99.5,
        "tags": ["vip", "beta_tester"],
        "nested": {"id": "20000000-0000-4000-8000-000000000001", "ip": "127.0.0.1"},
    }


def test_serialize_rows_maps_columns() -> None:
    rows = serialize_rows(
        ["name", "mrr", "period"],
        [("Apex FinTech", Decimal("16300.00"), Range(date(2026, 1, 1), date(2027, 1, 1), "[)"))],
    )
    assert rows == [{"name": "Apex FinTech", "mrr": 16300.0, "period": "[2026-01-01, 2027-01-01)"}]
