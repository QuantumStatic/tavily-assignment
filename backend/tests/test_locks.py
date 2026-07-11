import threading

from vendor_dd.engine.locks import KeyedLocks


def test_same_key_runs_one_at_a_time():
    locks = KeyedLocks()
    order: list[str] = []
    first_inside = threading.Event()
    release_first = threading.Event()

    def worker_a():
        with locks.acquire("voith.com"):
            order.append("a-enter")
            first_inside.set()
            assert release_first.wait(timeout=5)
            order.append("a-exit")

    def worker_b():
        assert first_inside.wait(timeout=5)   # ensure A holds the lock first
        with locks.acquire("voith.com"):
            order.append("b-enter")           # must come only after a-exit

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    assert first_inside.wait(timeout=5)
    release_first.set()
    ta.join(timeout=5)
    tb.join(timeout=5)

    assert order == ["a-enter", "a-exit", "b-enter"]   # B never interleaved with A


def test_different_keys_do_not_block_each_other():
    locks = KeyedLocks()
    b_ran = threading.Event()

    def worker_a():
        with locks.acquire("voith.com"):
            # hold "voith.com" and wait for B (on a different key) to prove it didn't block
            assert b_ran.wait(timeout=5), "different-key holder was blocked"

    def worker_b():
        with locks.acquire("jindal.com"):
            b_ran.set()

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    ta.join(timeout=5)
    tb.join(timeout=5)
    assert b_ran.is_set()


def test_lock_is_released_even_if_the_body_raises():
    locks = KeyedLocks()
    try:
        with locks.acquire("k"):
            raise ValueError("boom")
    except ValueError:
        pass
    # if the lock leaked, this second acquire in the same thread would deadlock
    with locks.acquire("k"):
        pass
