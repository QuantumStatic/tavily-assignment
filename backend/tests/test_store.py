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


def test_find_vendor_matches_case_and_whitespace_insensitively_within_project(tmp_path):
    store = _store(tmp_path)
    a = store.create_project("A")
    b = store.create_project("B")
    v = store.add_vendor(a.id, "Cives Steel")
    assert store.find_vendor(a.id, "  cives steel  ").id == v.id   # case/space-insensitive
    assert store.find_vendor(a.id, "Nucor") is None
    assert store.find_vendor(b.id, "Cives Steel") is None          # scoped to the project


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


def test_remove_vendor_keeps_cache_while_another_vendor_references_it(tmp_path):
    """The report cache is shared by domain across projects. Deleting the same vendor
    from one project must NOT wipe another project's still-present copy."""
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension

    db = tmp_path / "db.sqlite"
    store = Store(db, clock=lambda: "2026-07-09T00:00:00+00:00")
    pa = store.create_project("Project A")
    pb = store.create_project("Project B")
    va = store.add_vendor(pa.id, "Voith Hydro"); store.set_vendor_key(va.id, "voith.com")
    vb = store.add_vendor(pb.id, "Voith Hydro"); store.set_vendor_key(vb.id, "voith.com")

    cache = SQLiteCache(db)
    cache.put("voith hydro", Dimension.SNAPSHOT, {"name": "Voith"})
    cache.put("voith.com", Dimension.LEGAL, {"score": 2})

    store.remove_vendor(va.id)                       # delete from Project A only

    # Project B still references voith.com / "voith hydro" -> cache survives
    fresh = SQLiteCache(db)
    assert fresh.get("voith hydro", Dimension.SNAPSHOT) is not None
    assert fresh.get("voith.com", Dimension.LEGAL) is not None

    store.remove_vendor(vb.id)                       # now the last reference is gone
    gone = SQLiteCache(db)
    assert gone.get("voith hydro", Dimension.SNAPSHOT) is None
    assert gone.get("voith.com", Dimension.LEGAL) is None


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


def test_store_enforces_unique_vendor_name_per_project(tmp_path):
    import sqlite3

    import pytest

    store = _store(tmp_path)
    p = store.create_project("p")
    store.add_vendor(p.id, "Cives Steel")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_vendor(p.id, "  cives STEEL ")   # case/space variant of the same name
    # the same name in a different project is a legitimate new row
    p2 = store.create_project("p2")
    assert store.add_vendor(p2.id, "Cives Steel").id > 0


def _seed_cache(tmp_path, key: str):
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section

    cache = SQLiteCache(tmp_path / "db.sqlite")
    cache.put(key, Dimension.LEGAL,
              Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5).model_dump(mode="json"))
    cache.close()


def _cached_sections(tmp_path, key: str):
    from vendor_dd.engine.cache import SQLiteCache

    cache = SQLiteCache(tmp_path / "db.sqlite")
    rows = cache.all_sections(key)
    cache.close()
    return rows


def _seed_cache_with_reasoning(tmp_path, key: str, reasoning: str):
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section

    cache = SQLiteCache(tmp_path / "db.sqlite")
    cache.put(key, Dimension.LEGAL,
              Section(dimension=Dimension.LEGAL, findings=[], reasoning=reasoning, score=5).model_dump(mode="json"))
    cache.close()


def test_evict_unreferenced_clears_cache_when_no_vendor_row_references_it(tmp_path):
    store = _store(tmp_path)
    _seed_cache(tmp_path, "cives.com")
    store.evict_unreferenced("Cives Steel", "cives.com")
    assert _cached_sections(tmp_path, "cives.com") == {}


def test_evict_unreferenced_keeps_cache_while_a_vendor_still_references_it(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives.com")
    store.evict_unreferenced("Cives Steel", "cives.com")   # still referenced -> no-op
    assert _cached_sections(tmp_path, "cives.com") != {}


def test_remove_project_cascades_vendors_and_evicts_unreferenced_cache(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives.com")

    # a vendor in ANOTHER project shares the cache key — its report must survive
    p2 = store.create_project("p2")
    v2 = store.add_vendor(p2.id, "Cives Steel")
    store.set_vendor_key(v2.id, "cives.com")

    store.remove_project(p.id)
    assert store.get_project(p.id) is None
    assert store.list_vendors(p.id) == []
    assert _cached_sections(tmp_path, "cives.com") != {}   # still referenced by p2

    store.remove_project(p2.id)
    assert _cached_sections(tmp_path, "cives.com") == {}   # last reference gone -> evicted


def test_rename_vendor_updates_the_name_and_migrates_the_snapshot_cache_key(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Stel")           # typo
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives stel")                 # snapshot lives under the NAME key
    _seed_cache(tmp_path, "cives.com")                  # sections live under the domain key

    renamed = store.rename_vendor(v.id, "Cives Steel")
    assert renamed is not None and renamed.name == "Cives Steel"
    assert _cached_sections(tmp_path, "cives stel") == {}          # old name key gone
    assert _cached_sections(tmp_path, "cives steel") != {}         # moved to the new name
    assert _cached_sections(tmp_path, "cives.com") != {}           # domain sections untouched
    assert store.rename_vendor(9999, "x") is None


def test_rename_vendor_does_not_clobber_preexisting_cache_data_at_the_new_key(tmp_path):
    # report_cache is shared by NAME across projects. If some other vendor (in a
    # different project) already has a valid, correctly-fetched snapshot cached under
    # the target name, renaming a vendor into that name must not destroy it.
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Stel")           # typo
    store.set_vendor_key(v.id, "cives.com")

    _seed_cache_with_reasoning(tmp_path, "cives stel", "data being renamed away")
    _seed_cache_with_reasoning(tmp_path, "cives steel", "original target data")

    renamed = store.rename_vendor(v.id, "Cives Steel")
    assert renamed is not None and renamed.name == "Cives Steel"

    sections = _cached_sections(tmp_path, "cives steel")
    assert sections != {}
    from vendor_dd.engine.schemas import Dimension
    content, _ = sections[Dimension.LEGAL]
    assert content["reasoning"] == "original target data"

    assert _cached_sections(tmp_path, "cives stel") == {}          # old key cleared out
