from datetime import datetime, timedelta, timezone
from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.cache import SQLiteCache


def _now():
    return datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def test_put_then_get_fresh_returns_payload(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5})
    got = cache.get("acme.com", Dimension.LEGAL)
    assert got == {"score": 5}


def test_get_expired_returns_none(tmp_path):
    t = {"now": _now()}
    cache = SQLiteCache(tmp_path / "c.db", clock=lambda: t["now"])
    cache.put("acme.com", Dimension.NEWS_POSITIVE, {"score": 9})  # TTL = 1 day
    t["now"] = _now() + timedelta(days=2)                          # advance past TTL
    assert cache.get("acme.com", Dimension.NEWS_POSITIVE) is None


def test_get_missing_returns_none(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    assert cache.get("acme.com", Dimension.LEGAL) is None


def test_fetched_at_exposed_for_as_of(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.SNAPSHOT, {"x": 1})
    assert cache.fetched_at("acme.com", Dimension.SNAPSHOT) == _now()
