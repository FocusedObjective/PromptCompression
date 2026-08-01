from concurrent.futures import ThreadPoolExecutor

import pytest

from app.demo_access import DemoAccessError, DemoSessionManager


SIGNING_KEY = "demo-signing-key-with-at-least-thirty-two-bytes"


def build_manager(
    *,
    now: list[float] | None = None,
    max_operations: int = 2,
    max_active_sessions: int = 2,
) -> DemoSessionManager:
    current = now if now is not None else [1_000.0]
    return DemoSessionManager(
        enabled=True,
        signing_key=SIGNING_KEY,
        mode_expires_at=2_000,
        session_ttl_seconds=60,
        max_operations=max_operations,
        max_input_chars=100,
        max_input_chars_per_operation=60,
        max_active_sessions=max_active_sessions,
        clock=lambda: current[0],
    )


def test_demo_session_is_signed_short_lived_and_bounded() -> None:
    manager = build_manager()
    session = manager.issue_session()

    assert session.token.startswith("demo-v1.")
    assert session.expires_at == 1_060
    assert session.max_operations == 2
    assert session.max_input_chars == 100
    assert session.max_input_chars_per_operation == 60

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
    session = manager.issue_session()

    with pytest.raises(DemoAccessError) as caught:
        manager.validate_authorization_header(f"Bearer {session.token}x")
    assert caught.value.status_code == 401

    authorization = manager.validate_authorization_header(
        f"Bearer {session.token}"
    )
    with pytest.raises(DemoAccessError) as caught:
        manager.reserve_operation(authorization, input_chars=61)
    assert caught.value.status_code == 413


def test_demo_sessions_expire_and_active_capacity_is_reclaimed() -> None:
    now = [1_000.0]
    manager = build_manager(now=now, max_active_sessions=1)
    first = manager.issue_session()

    with pytest.raises(DemoAccessError) as caught:
        manager.issue_session()
    assert caught.value.status_code == 429

    now[0] = 1_061.0
    second = manager.issue_session()
    assert second.token != first.token
    with pytest.raises(DemoAccessError) as caught:
        manager.validate_authorization_header(f"Bearer {first.token}")
    assert caught.value.status_code == 401


def test_disabled_or_expired_demo_mode_fails_closed() -> None:
    disabled = DemoSessionManager(enabled=False)
    with pytest.raises(DemoAccessError) as caught:
        disabled.issue_session()
    assert caught.value.status_code == 503

    expired = DemoSessionManager(
        enabled=True,
        signing_key=SIGNING_KEY,
        mode_expires_at=999,
        clock=lambda: 1_000,
    )
    assert expired.enabled is False
    with pytest.raises(DemoAccessError) as caught:
        expired.issue_session()
    assert caught.value.status_code == 503


def test_concurrent_reservations_cannot_exceed_operation_allowance() -> None:
    manager = build_manager(max_operations=5)
    session = manager.issue_session()
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
