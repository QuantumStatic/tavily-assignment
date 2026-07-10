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


def test_put_persists_and_returns_sources_via_all_sections(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5},
              sources=[{"url": "https://a.com", "score": 0.7}])
    got = cache.all_sections("acme.com")
    assert Dimension.LEGAL in got
    content, fetched = got[Dimension.LEGAL]
    assert content == {"score": 5}
    assert fetched == _now()


def test_all_sections_ignores_ttl_and_returns_everything(tmp_path):
    t = {"now": _now()}
    cache = SQLiteCache(tmp_path / "c.db", clock=lambda: t["now"])
    cache.put("acme.com", Dimension.NEWS_POSITIVE, {"score": 9})  # 1-day TTL
    cache.put("acme.com", Dimension.LEGAL, {"score": 4})
    t["now"] = _now() + timedelta(days=30)  # everything is now stale for get()
    assert cache.get("acme.com", Dimension.NEWS_POSITIVE) is None  # get() honors TTL
    got = cache.all_sections("acme.com")                            # all_sections does not
    assert set(got) == {Dimension.NEWS_POSITIVE, Dimension.LEGAL}


def test_put_without_sources_still_works(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.SNAPSHOT, {"name": "Acme"})   # no sources arg
    assert cache.get("acme.com", Dimension.SNAPSHOT) == {"name": "Acme"}


def test_close_closes_connection(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.SNAPSHOT, {"name": "Acme"})
    cache.close()
    import sqlite3
    try:
        cache.get("acme.com", Dimension.SNAPSHOT)
        assert False, "expected an error after close()"
    except sqlite3.ProgrammingError:
        pass


def test_cache_logs_sql_queries(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5})
    lines = [json.loads(l) for l in (tmp_path / "logs" / "db.log").read_text().splitlines() if l.strip()]
    assert any(o["event"] == "db.query" and "report_cache" in o["payload"]["sql"] for o in lines)


def test_cache_enables_wal_and_busy_timeout(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    mode = cache._conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = cache._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 3000
