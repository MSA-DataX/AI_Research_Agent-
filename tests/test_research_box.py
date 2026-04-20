from __future__ import annotations


def test_create_and_load(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("test task")
    assert rb.id
    assert rb.task == "test task"
    assert rb.status == "running"

    loaded = rb_store.load(rb.id)
    assert loaded is not None
    assert loaded.id == rb.id
    assert loaded.task == "test task"


def test_add_sources_and_visited(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("t")
    rb.add_sources(["https://a.com", "https://b.com", "https://a.com"])
    assert len(rb.sources) == 2

    rb.mark_visited("https://a.com")
    rb.mark_visited("https://a.com")
    assert len(rb.visited_sources) == 1

    rb.add_sources(["https://www.a.com/?utm_source=x"])
    assert len(rb.sources) == 2


def test_save_roundtrip(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("t2")
    rb.extracted_data = [{"name": "X", "source_url": "https://x.com"}]
    rb.entities = {"emails": ["a@b.de"]}
    rb.validation = {"confidence": 42, "label": "low"}
    rb.save()

    loaded = rb_store.load(rb.id)
    assert loaded.extracted_data == [{"name": "X", "source_url": "https://x.com"}]
    assert loaded.entities["emails"] == ["a@b.de"]
    assert loaded.validation["confidence"] == 42


def test_list_all_orders_by_updated(tmp_db):
    import research_box as rb_store
    a = rb_store.create("first")
    b = rb_store.create("second")
    all_rbs = rb_store.list_all()
    assert len(all_rbs) == 2
    ids = [r.id for r in all_rbs]
    assert b.id in ids and a.id in ids


def test_delete(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("doomed")
    assert rb_store.delete(rb.id) is True
    assert rb_store.load(rb.id) is None
    assert rb_store.delete(rb.id) is False


def test_find_similar_reuses_close_task(tmp_db, fake_embed):
    import research_box as rb_store
    a = rb_store.create("deutsche KI startups")
    rb_store.create("berliner wohnungsbau")
    found = rb_store.find_similar("deutsche KI startups")
    assert found is not None
    assert found.id == a.id


def test_find_similar_returns_none_below_threshold(tmp_db, monkeypatch):
    import research_box as rb_store

    def orthogonal(t: str) -> list[float]:
        return [1.0, 0.0, 0.0] if "alpha" in t else [0.0, 1.0, 0.0]

    monkeypatch.setattr(rb_store, "embed", orthogonal)
    rb_store.create("topic alpha something")
    found = rb_store.find_similar("beta topic unrelated")
    assert found is None


def test_validation_history_appends_snapshots(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("t")
    rb.validation = {"confidence": 50, "label": "low", "mode": "validate", "total": 2, "supported": 1}
    rb.append_validation_snapshot()
    rb.validation = {"confidence": 80, "label": "high", "mode": "verify", "total": 2, "supported": 2}
    rb.append_validation_snapshot()
    rb.save()

    loaded = rb_store.load(rb.id)
    assert len(loaded.validation_history) == 2
    assert loaded.validation_history[0]["confidence"] == 50
    assert loaded.validation_history[1]["confidence"] == 80
    assert loaded.validation_history[1]["mode"] == "verify"


def test_validation_history_capped_at_20(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("t")
    for i in range(30):
        rb.validation = {"confidence": i, "label": "low", "mode": "validate"}
        rb.append_validation_snapshot()
    assert len(rb.validation_history) == 20
    assert rb.validation_history[0]["confidence"] == 10
    assert rb.validation_history[-1]["confidence"] == 29


def test_validation_history_ignores_empty_validation(tmp_db):
    import research_box as rb_store
    rb = rb_store.create("t")
    rb.validation = {}
    rb.append_validation_snapshot()
    assert rb.validation_history == []
