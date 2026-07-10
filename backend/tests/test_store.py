from vendor_dd.surfaces.api.store import Store


def _store(tmp_path):
    return Store(tmp_path / "db.sqlite", clock=lambda: "2026-07-09T00:00:00+00:00")


def test_create_and_list_projects(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    assert p.id > 0 and p.name == "Bridge job" and p.created_at == "2026-07-09T00:00:00+00:00"
    assert [x.id for x in store.list_projects()] == [p.id]
    assert store.get_project(p.id).name == "Bridge job"
    assert store.get_project(9999) is None


def test_add_list_remove_vendors(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    assert v.project_id == p.id and v.name == "Cives Steel" and v.vendor_key is None
    assert [x.id for x in store.list_vendors(p.id)] == [v.id]
    assert store.get_vendor(v.id).name == "Cives Steel"
    store.remove_vendor(v.id)
    assert store.list_vendors(p.id) == []
    assert store.get_vendor(v.id) is None


def test_remove_vendor_clears_its_cached_research(tmp_path):
    """Deleting a vendor must evict its cache (both the name-keyed snapshot and the
    domain-keyed sections) so a re-add re-runs fresh instead of serving stale results."""
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension

    db = tmp_path / "db.sqlite"
    store = Store(db, clock=lambda: "2026-07-09T00:00:00+00:00")
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Voith Hydro")
    store.set_vendor_key(v.id, "voith.com")

    # Populate the shared cache the way the pipeline does: snapshot under the input
    # name, sections under the resolved domain.
    cache = SQLiteCache(db)
    cache.put("voith hydro", Dimension.SNAPSHOT, {"name": "Voith"})
    cache.put("voith.com", Dimension.LEGAL, {"score": 2})
    cache.put("voith.com", Dimension.FINANCIAL, {"score": 0})
    assert cache.get("voith hydro", Dimension.SNAPSHOT) is not None
    assert cache.get("voith.com", Dimension.LEGAL) is not None

    store.remove_vendor(v.id)

    fresh = SQLiteCache(db)
    assert fresh.get("voith hydro", Dimension.SNAPSHOT) is None
    assert fresh.get("voith.com", Dimension.LEGAL) is None
    assert fresh.get("voith.com", Dimension.FINANCIAL) is None


def test_remove_vendor_without_cache_table_does_not_crash(tmp_path):
    store = _store(tmp_path)              # fresh DB, no report ever run
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    store.remove_vendor(v.id)            # must not raise "no such table: report_cache"
    assert store.get_vendor(v.id) is None


def test_set_vendor_key_backfill(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    assert store.get_vendor(v.id).vendor_key == "cives.com"


def test_create_project_persists_session_id(tmp_path):
    store = Store(tmp_path / "db.sqlite",
                  clock=lambda: "2026-07-10T00:00:00+00:00",
                  id_gen=lambda: "sess-abc")
    p = store.create_project("Bridge job")
    assert p.session_id == "sess-abc"
    assert store.get_project(p.id).session_id == "sess-abc"
    assert store.list_projects()[0].session_id == "sess-abc"


def test_session_id_defaults_to_uuid_when_no_id_gen(tmp_path):
    store = Store(tmp_path / "db.sqlite")           # real uuid id_gen
    a = store.create_project("A")
    b = store.create_project("B")
    assert a.session_id and b.session_id and a.session_id != b.session_id
    assert len(a.session_id) == 32                  # uuid4 hex is 32 chars
    int(a.session_id, 16)                           # valid hex


def test_migrates_pre_session_id_database(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE projects (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT NOT NULL,
             created_at TEXT NOT NULL
           )"""
    )
    conn.execute("INSERT INTO projects (name, created_at) VALUES (?,?)", ("Old Project", "t"))
    conn.commit()
    conn.close()

    store = Store(path)  # should migrate in __init__ without crashing
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "Old Project"
    assert projects[0].session_id is None   # pre-existing row has no session_id

    # new projects on the migrated DB get a real session_id
    p = store.create_project("New Project")
    assert p.session_id is not None


def test_store_logs_sql_queries(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    store = _store(tmp_path)
    store.create_project("Acme")
    lines = [json.loads(l) for l in (tmp_path / "logs" / "db.log").read_text().splitlines() if l.strip()]
    assert any(o["event"] == "db.query" and "projects" in o["payload"]["sql"] for o in lines)


def test_store_enables_wal_and_busy_timeout(tmp_path):
    store = _store(tmp_path)
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 3000
