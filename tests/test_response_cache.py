from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from app.response_cache import LocalResponseCache


def test_byte_limit_evicts_least_recently_used_entry() -> None:
    cache = LocalResponseCache(
        max_bytes=800,
        max_entry_bytes=800,
        max_entries=10,
        ttl_seconds=60,
    )

    assert cache.put("first", b"a" * 100)
    assert cache.put("second", b"b" * 100)
    assert cache.get("first") == b"a" * 100
    assert cache.put("third", b"c" * 100)

    assert cache.get("second") is None
    assert cache.get("first") == b"a" * 100
    assert cache.get("third") == b"c" * 100
    assert cache.stats()["bytes"] <= 800


def test_oversized_entry_is_not_stored() -> None:
    cache = LocalResponseCache(
        max_bytes=1024,
        max_entry_bytes=300,
        ttl_seconds=60,
    )

    assert cache.put("key", b"x" * 100) is False
    assert cache.stats()["entries"] == 0


def test_single_flight_shares_one_concurrent_computation() -> None:
    cache = LocalResponseCache(
        max_bytes=4096,
        max_entry_bytes=2048,
        ttl_seconds=60,
    )
    computation_started = Event()
    release_computation = Event()
    call_lock = Lock()
    calls = 0

    def compute() -> tuple[bytes, bool]:
        nonlocal calls
        with call_lock:
            calls += 1
        computation_started.set()
        assert release_computation.wait(timeout=2)
        return b'{"result":"compressed"}', True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_compute, "same-key", compute)
        assert computation_started.wait(timeout=2)
        second = executor.submit(cache.get_or_compute, "same-key", compute)
        release_computation.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert calls == 1
    assert {result.status for result in results} == {"store", "shared"}
    assert all(result.payload == b'{"result":"compressed"}' for result in results)


def test_expired_entry_is_a_miss() -> None:
    now = 100.0
    cache = LocalResponseCache(
        max_bytes=4096,
        max_entry_bytes=2048,
        ttl_seconds=5,
        clock=lambda: now,
    )
    assert cache.put("key", b"value")

    now = 106.0

    assert cache.get("key") is None
    assert cache.stats()["entries"] == 0
