from vendor_dd.surfaces.api.runs import DONE, ReportRun, RunRegistry


def _drain(q):
    out = []
    while True:
        ev = q.get(timeout=1)
        if ev is DONE:
            return out
        out.append(ev)


def test_subscriber_receives_published_events_then_done():
    run = ReportRun()
    q = run.subscribe()
    run.publish("a")
    run.publish("b")
    run.finish()
    assert _drain(q) == ["a", "b"]


def test_late_subscriber_gets_history_replayed_before_live_events():
    run = ReportRun()
    run.publish("a")
    q = run.subscribe()          # joins after "a" was published
    run.publish("b")
    run.finish()
    assert _drain(q) == ["a", "b"]


def test_subscribing_after_finish_yields_full_history_then_done():
    run = ReportRun()
    run.publish("a")
    run.finish()
    assert _drain(run.subscribe()) == ["a"]


def test_registry_returns_the_same_run_while_in_flight():
    reg = RunRegistry()
    run1, created1 = reg.get_or_create(7)
    run2, created2 = reg.get_or_create(7)
    assert created1 is True and created2 is False and run1 is run2
    reg.remove(7)
    run3, created3 = reg.get_or_create(7)
    assert created3 is True and run3 is not run1


def test_registry_remove_is_idempotent():
    reg = RunRegistry()
    reg.remove(42)   # never registered — must not raise
