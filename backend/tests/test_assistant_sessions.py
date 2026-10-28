from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.core.errors import BizException, ErrorCode
from app.services.assistant_sessions import AssistantSessionRegistry


class FakeClock:
    def __init__(self):
        self.monotonic = 100.0
        self.current = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.monotonic

    def utcnow(self):
        return self.current

    def advance(self, seconds: float):
        self.monotonic += seconds
        from datetime import timedelta

        self.current += timedelta(seconds=seconds)


def test_session_expiry_removes_metadata_and_has_no_chat_payload():
    clock = FakeClock()
    registry = AssistantSessionRegistry(clock=clock, utcnow=clock.utcnow)
    contract = registry.create(
        staff_id=7, company_id=3, login_session_id="login-a", ttl_seconds=5
    )
    assert contract.expires_at.isoformat().startswith("2026-10-10T00:00:05")

    record = registry._sessions[contract.session_id]
    assert set(record.__dataclass_fields__) == {
        "session_id",
        "staff_id",
        "company_id",
        "login_session_id",
        "version",
        "expires_at",
        "deadline",
    }

    clock.advance(5)
    with pytest.raises(BizException) as expired:
        registry.validate(
            session_id=contract.session_id,
            version=contract.version,
            staff_id=7,
            company_id=3,
            login_session_id="login-a",
        )
    assert expired.value.code == ErrorCode.AI_SESSION_EXPIRED
    assert contract.session_id not in registry._sessions


def test_session_is_bound_to_both_staff_and_company_and_version():
    registry = AssistantSessionRegistry()
    contract = registry.create(
        staff_id=7, company_id=3, login_session_id="login-a", ttl_seconds=60
    )

    for staff_id, company_id, login_id in [
        (8, 3, "login-a"),
        (7, 4, "login-a"),
        (7, 3, "login-b"),
    ]:
        with pytest.raises(BizException) as forbidden:
            registry.validate(
                session_id=contract.session_id,
                version=contract.version,
                staff_id=staff_id,
                company_id=company_id,
                login_session_id=login_id,
            )
        assert forbidden.value.code == ErrorCode.FORBIDDEN

    refreshed = registry.clear(
        session_id=contract.session_id,
        version=contract.version,
        staff_id=7,
        company_id=3,
        login_session_id="login-a",
        ttl_seconds=60,
    )
    assert refreshed.version == contract.version + 1
    with pytest.raises(BizException) as stale:
        registry.validate(
            session_id=contract.session_id,
            version=contract.version,
            staff_id=7,
            company_id=3,
            login_session_id="login-a",
        )
    assert stale.value.code == ErrorCode.AI_SESSION_VERSION_CONFLICT


def test_active_session_validation_reaps_other_expired_metadata():
    clock = FakeClock()
    registry = AssistantSessionRegistry(clock=clock, utcnow=clock.utcnow)
    expired = registry.create(
        staff_id=7, company_id=3, login_session_id="login-a", ttl_seconds=1
    )
    active = registry.create(
        staff_id=7, company_id=3, login_session_id="login-a", ttl_seconds=30
    )
    clock.advance(2)

    registry.validate(
        session_id=active.session_id,
        version=active.version,
        staff_id=7,
        company_id=3,
        login_session_id="login-a",
    )
    assert expired.session_id not in registry._sessions


def test_concurrent_creation_and_clear_are_atomic():
    registry = AssistantSessionRegistry()
    with ThreadPoolExecutor(max_workers=12) as pool:
        sessions = list(
            pool.map(
                lambda _: registry.create(
                    staff_id=7,
                    company_id=3,
                    login_session_id="login-a",
                    ttl_seconds=60,
                ),
                range(48),
            )
        )
    assert len({item.session_id for item in sessions}) == len(sessions)

    initial = sessions[0]

    def clear_once(_):
        try:
            return registry.clear(
                session_id=initial.session_id,
                version=initial.version,
                staff_id=7,
                company_id=3,
                login_session_id="login-a",
                ttl_seconds=60,
            )
        except BizException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(clear_once, range(24)))
    successful = [item for item in results if not isinstance(item, BizException)]
    conflicts = [item for item in results if isinstance(item, BizException)]
    assert len(successful) == 1
    assert successful[0].version == 2
    assert len(conflicts) == 23
    assert all(item.code == ErrorCode.AI_SESSION_VERSION_CONFLICT for item in conflicts)
