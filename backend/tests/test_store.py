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


def test_set_vendor_key_backfill(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    assert store.get_vendor(v.id).vendor_key == "cives.com"
