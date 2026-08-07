from concurrent.futures import ThreadPoolExecutor

import pytest

from app.demo_access import (
    DemoAccessError,
    DemoSessionManager,
    InMemoryDemoAccessStore,
    demo_client_identifier,
)


SIGNING_KEY = "demo-signing-key-with-at-least-thirty-two-bytes"


def build_manager(
    *,
    now: list[float] | None = None,
    max_operations: int = 2,
    rate_limit_sessions: int = 10,
    rate_limit_window_seconds: int = 60,
    max_sessions_per_client_per_day: int = 10,
    max_operations_per_client_per_day: int = 100,
    max_input_chars_per_client_per_day: int = 1_000,
    max_sessions_per_day: int = 20,
    max_operations_per_day: int = 200,
    max_input_chars_per_day: int = 2_000,
    store: InMemoryDemoAccessStore | None = None,
) -> DemoSessionManager:
    current = now if now is not None else [1_000.0]
    return DemoSessionManager(
        enabled=True,
        signing_key=SIGNING_KEY,
        session_ttl_seconds=60,
        max_operations=max_operations,
        max_input_chars=100,
        max_input_chars_per_operation=60,
        rate_limit_sessions=rate_limit_sessions,
        rate_limit_window_seconds=rate_limit_window_seconds,
        max_sessions_per_client_per_day=max_sessions_per_client_per_day,
        max_operations_per_client_per_day=max_operations_per_client_per_day,
        max_input_chars_per_client_per_day=max_input_chars_per_client_per_day,
        max_sessions_per_day=max_sessions_per_day,
        max_operations_per_day=max_operations_per_day,
        max_input_chars_per_day=max_input_chars_per_day,
        store=store,
        clock=lambda: current[0],
    )


def test_demo_session_is_signed_short_lived_and_bounded() -> None:
    manager = build_manager()
    session = manager.issue_session("client-a")

    assert session.token.startswith("demo-v1.")
    assert session.expires_at == 1_060
    assert session.max_operations == 2
    assert session.max_input_chars == 100
    assert session.max_input_chars_per_operation == 60
    assert session.daily_sessions_remaining == 9
    assert session.daily_operations_remaining == 100

    authorization = manager.validate_authorization_header(
        f"Bearer {session.token}"
    )
    manager.reserve_operation(authorization, input_chars=40)
    manager.reserve_operation(authorization, input_chars=40)

    with pytest.raises(DemoAccessError) as caught:
        manager.reserve_operation(authorization, input_chars=1)
    assert caught.value.status_code == 429
    assert session.token not in str(caught.value)
    assert SIGNING_KEY not in str(caught.value)


def test_demo_session_rejects_tampering_and_oversized_input() -> None:
    manager = build_manager()
    session = manager.issue_session("client-a")

    with pytest.raises(DemoAccessError) as caught:
        manager.validate_authorization_header(f"Bearer {session.token}x")
    assert caught.value.status_code == 401

    authorization = manager.validate_authorization_header(
        f"Bearer {session.token}"
    )
    with pytest.raises(DemoAccessError) as caught:
        manager.reserve_operation(authorization, input_chars=61)
    assert caught.value.status_code == 413


def test_demo_sessions_expire_without_a_global_date_limit() -> None:
    now = [1_000.0]
    manager = build_manager(now=now)
    first = manager.issue_session("client-a")

    now[0] = 1_061.0
    second = manager.issue_session("client-a")

    assert manager.enabled is True
    assert second.token != first.token
    with pytest.raises(DemoAccessError) as caught:
        manager.validate_authorization_header(f"Bearer {first.token}")
    assert caught.value.status_code == 401


def test_disabled_demo_mode_fails_closed() -> None:
    disabled = DemoSessionManager(enabled=False)
    with pytest.raises(DemoAccessError) as caught:
        disabled.issue_session("client-a")
    assert caught.value.status_code == 503


def test_obsolete_global_expiry_environment_value_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("USAGETAP_DEMO_MODE_ENABLED", "true")
    monkeypatch.setenv("USAGETAP_DEMO_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("USAGETAP_DEMO_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("USAGETAP_DEMO_MODE_EXPIRES_AT", "2000-01-01T00:00:00Z")

    manager = DemoSessionManager.from_environment()

    assert manager.enabled is True
    assert manager.issue_session("client-a").token.startswith("demo-v1.")


def test_concurrent_reservations_cannot_exceed_operation_allowance() -> None:
    manager = build_manager(max_operations=5)
    session = manager.issue_session("client-a")
    authorization = manager.validate_authorization_header(
        f"Bearer {session.token}"
    )

    def reserve() -> int:
        try:
            manager.reserve_operation(authorization, input_chars=1)
        except DemoAccessError as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=12) as executor:
        statuses = list(executor.map(lambda _index: reserve(), range(12)))

    assert statuses.count(200) == 5
    assert statuses.count(429) == 7


def test_session_issuance_is_rate_limited_per_client() -> None:
    manager = build_manager(
        rate_limit_sessions=2,
        rate_limit_window_seconds=60,
    )
    manager.issue_session("client-a")
    manager.issue_session("client-a")

    with pytest.raises(DemoAccessError) as caught:
        manager.issue_session("client-a")

    assert caught.value.status_code == 429
    assert caught.value.retry_after_seconds == 20
    assert "network" in caught.value.public_message
    assert manager.issue_session("client-b").token.startswith("demo-v1.")


def test_daily_session_quota_resets_at_utc_midnight() -> None:
    now = [1_000.0]
    manager = build_manager(
        now=now,
        max_sessions_per_client_per_day=2,
        rate_limit_sessions=10,
    )
    manager.issue_session("client-a")
    manager.issue_session("client-a")

    with pytest.raises(DemoAccessError) as caught:
        manager.issue_session("client-a")
    assert caught.value.status_code == 429
    assert caught.value.retry_after_seconds == 85_400

    now[0] = 86_401.0
    assert manager.issue_session("client-a").daily_sessions_remaining == 1


def test_daily_operation_quota_applies_across_sessions() -> None:
    manager = build_manager(
        max_operations=5,
        max_operations_per_client_per_day=2,
    )
    first = manager.issue_session("client-a")
    second = manager.issue_session("client-a")
    first_auth = manager.validate_authorization_header(f"Bearer {first.token}")
    second_auth = manager.validate_authorization_header(f"Bearer {second.token}")
    manager.reserve_operation(first_auth, input_chars=1)
    manager.reserve_operation(second_auth, input_chars=1)

    with pytest.raises(DemoAccessError) as caught:
        manager.reserve_operation(first_auth, input_chars=1)

    assert caught.value.status_code == 429
    assert "operation quota" in caught.value.public_message


def test_global_daily_quota_applies_across_clients() -> None:
    manager = build_manager(
        max_sessions_per_client_per_day=2,
        max_sessions_per_day=2,
    )
    manager.issue_session("client-a")
    manager.issue_session("client-b")

    with pytest.raises(DemoAccessError) as caught:
        manager.issue_session("client-c")

    assert caught.value.status_code == 429
    assert caught.value.public_message == "Today's demo capacity has been reached."


def test_shared_store_keeps_sessions_valid_across_manager_restart() -> None:
    store = InMemoryDemoAccessStore()
    first_manager = build_manager(store=store)
    session = first_manager.issue_session("client-a")
    restarted_manager = build_manager(store=store)

    authorization = restarted_manager.validate_authorization_header(
        f"Bearer {session.token}"
    )
    restarted_manager.reserve_operation(authorization, input_chars=1)


def test_client_identifier_uses_trusted_cloud_run_suffix() -> None:
    assert demo_client_identifier(
        "203.0.113.99, 198.51.100.8, 192.0.2.10",
        "10.0.0.1",
        trust_forwarded_for=True,
    ) == "198.51.100.8"
    assert demo_client_identifier(
        "203.0.113.99, 198.51.100.8, 192.0.2.10",
        "10.0.0.1",
        trust_forwarded_for=False,
    ) == "10.0.0.1"


def test_invalid_forwarded_header_falls_back_to_direct_host() -> None:
    assert demo_client_identifier(
        "attacker-controlled, not-an-ip",
        "10.0.0.1",
        trust_forwarded_for=True,
    ) == "10.0.0.1"
