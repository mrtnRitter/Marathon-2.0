"""Database tests for workflow and macro editing locks."""

import hashlib
from datetime import datetime, timedelta, timezone
import queue
import threading

import pytest
from sqlalchemy import text

from marathon.server.auth.sessions import AuthUser
from marathon.server.db.edit_locks import (
    LockHeld,
    LockMissing,
    lock_missing_text,
    acquire_lock,
    active_foreign_lock,
    break_lock,
    heartbeat_s,
    lock_status,
    release_lock,
    require_lock,
)


@pytest.fixture
def lock_users(db_engine):
    users = [
        AuthUser("lock-user-a", "a@example.test", "Alice", "user", "session-a"),
        AuthUser("lock-user-b", "b@example.test", "Bob", "user", "session-b"),
    ]
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES (:id, :email, :name, 'user')"),
            [
                {"id": user.id, "email": user.email, "name": user.display_name}
                for user in users
            ],
        )
    return users


def test_acquire_new_and_heartbeat(db_engine, lock_users):
    user = lock_users[0]
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        acquired = acquire_lock(connection, user, "workflow", "wf-a", None, 120, now)
        assert acquired == {
            "token": acquired["token"],
            "expires_at": now + timedelta(seconds=120),
            "heartbeat_s": 30,
        }
        stored_hash = connection.execute(
            text("SELECT token_hash FROM edit_locks WHERE object_type = 'workflow' AND object_id = 'wf-a'")
        ).scalar_one()
        assert stored_hash == hashlib.sha256(acquired["token"].encode("utf-8")).hexdigest()
        require_lock(connection, "workflow", "wf-a", acquired["token"])

        renewed = acquire_lock(connection, user, "workflow", "wf-a", acquired["token"], 120, now + timedelta(seconds=10))

    assert renewed["token"] == acquired["token"]
    assert renewed["expires_at"] == now + timedelta(seconds=130)
    assert heartbeat_s(120) == 30
    assert heartbeat_s(10) == 5


def test_naive_now_is_normalized_to_utc(db_engine, lock_users):
    user = lock_users[0]
    now = datetime(2025, 1, 1)
    with db_engine.begin() as connection:
        lock = acquire_lock(connection, user, "workflow", "wf-naive-time", None, 30, now)
        assert lock["expires_at"] == datetime(2025, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        broken = break_lock(connection, "workflow", "wf-naive-time", now + timedelta(seconds=30))
    assert broken["expired"] is True


def test_acquire_retries_missing_row_without_recursion(lock_users):
    class Result:
        def __init__(self, rowcount=0):
            self.rowcount = rowcount

        def mappings(self):
            return self

        def first(self):
            return None

    class Connection:
        def __init__(self):
            self.inserts = 0

        def execute(self, statement, _parameters):
            if str(statement).startswith("INSERT INTO edit_locks"):
                self.inserts += 1
                return Result(int(self.inserts == 1101))
            return Result()

    connection = Connection()
    acquired = acquire_lock(connection, lock_users[0], "workflow", "wf-retry", None, 120)
    assert acquired["token"]
    assert connection.inserts == 1101


def test_foreign_and_same_user_without_token_are_held(db_engine, lock_users):
    assert str(LockMissing()) == lock_missing_text
    with db_engine.begin() as connection:
        acquire_lock(connection, lock_users[0], "workflow", "wf-held", None, 120)
        with pytest.raises(LockHeld, match="Wird gerade bearbeitet von Alice"):
            acquire_lock(connection, lock_users[1], "workflow", "wf-held", None, 120)
        with pytest.raises(LockHeld, match="Wird gerade bearbeitet von Alice"):
            acquire_lock(connection, lock_users[0], "workflow", "wf-held", None, 120)


def test_expired_lock_is_taken_over_and_old_token_invalid(db_engine, lock_users):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        old = acquire_lock(connection, lock_users[0], "macro", "macro-expired", None, 30, now)
        new = acquire_lock(
            connection, lock_users[1], "macro", "macro-expired", None, 30, now + timedelta(seconds=31)
        )
        with pytest.raises(LockMissing):
            require_lock(connection, "macro", "macro-expired", old["token"])
        require_lock(connection, "macro", "macro-expired", new["token"])


def test_expired_own_token_satisfies_require_and_release_requires_token(db_engine, lock_users):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        lock = acquire_lock(connection, lock_users[0], "workflow", "wf-release", None, 30, now)
        connection.execute(
            text("UPDATE edit_locks SET expires_at = :expires_at WHERE object_type = 'workflow' AND object_id = 'wf-release'"),
            {"expires_at": now - timedelta(seconds=1)},
        )
        require_lock(connection, "workflow", "wf-release", lock["token"])
        release_lock(connection, "workflow", "wf-release", "wrong-token")
        require_lock(connection, "workflow", "wf-release", lock["token"])
        release_lock(connection, "workflow", "wf-release", lock["token"])
        with pytest.raises(LockMissing):
            require_lock(connection, "workflow", "wf-release", lock["token"])


def test_break_lock_returns_holder_and_invalidates_token(db_engine, lock_users):
    with db_engine.begin() as connection:
        lock = acquire_lock(connection, lock_users[0], "workflow", "wf-break", None, 120)
        assert break_lock(connection, "workflow", "wf-break") == {
            "user_id": lock_users[0].id,
            "name": "Alice",
            "expired": False,
        }
        with pytest.raises(LockMissing):
            require_lock(connection, "workflow", "wf-break", lock["token"])
        assert break_lock(connection, "workflow", "missing") is None
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        acquire_lock(connection, lock_users[0], "workflow", "wf-break-expired", None, 30, now)
        assert break_lock(connection, "workflow", "wf-break-expired", now + timedelta(seconds=30)) == {
            "user_id": lock_users[0].id,
            "name": "Alice",
            "expired": True,
        }


def test_status_and_foreign_lock_ignore_expired(db_engine, lock_users):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        acquire_lock(connection, lock_users[0], "workflow", "wf-status", None, 30, now)
        acquire_lock(connection, lock_users[1], "workflow", "wf-foreign", None, 30, now)
        status = lock_status(connection, "workflow", "wf-status", now + timedelta(seconds=30))
        assert status == {"holder": None, "expires_at": None}
        assert active_foreign_lock(connection, "workflow", "wf-status", now + timedelta(seconds=30)) is None
        assert active_foreign_lock(connection, "workflow", "wf-foreign", now) == {
            "user_id": lock_users[1].id,
            "name": "Bob",
        }
        assert lock_status(connection, "workflow", "wf-foreign", now)["holder"] == {
            "id": lock_users[1].id,
            "email": lock_users[1].email,
            "display_name": "Bob",
        }



def test_holder_name_falls_back_to_email(db_engine, lock_users):
    user = lock_users[0]
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE users SET display_name = '' WHERE id = :id"), {"id": user.id})
        acquire_lock(connection, user, "macro", "macro-email-holder", None, 120)
        assert active_foreign_lock(connection, "macro", "macro-email-holder") == {
            "user_id": user.id,
            "name": user.email,
        }
        with pytest.raises(LockHeld, match=user.email):
            acquire_lock(connection, lock_users[1], "macro", "macro-email-holder", None, 120)


def test_deleting_user_cascades_lock(db_engine, lock_users):
    with db_engine.begin() as connection:
        lock = acquire_lock(connection, lock_users[0], "macro", "macro-user-delete", None, 120)
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": lock_users[0].id})
        with pytest.raises(LockMissing):
            require_lock(connection, "macro", "macro-user-delete", lock["token"])


def test_concurrent_acquire_has_exactly_one_winner(db_engine, lock_users):
    barrier = threading.Barrier(2, timeout=5)
    results = queue.Queue()

    def acquire(user):
        try:
            with db_engine.connect() as connection:
                with connection.begin():
                    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                    barrier.wait(timeout=5)
                    results.put(("ok", acquire_lock(connection, user, "workflow", "wf-concurrent", None, 120)))
        except LockHeld as error:
            results.put(("held", str(error)))
        except BaseException as error:
            results.put(("error", repr(error)))

    threads = [threading.Thread(target=acquire, args=(user,), daemon=True) for user in lock_users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads), "concurrent acquire exceeded 10 seconds"
    outcomes = [results.get_nowait() for _ in threads]
    assert sorted(result[0] for result in outcomes) == ["held", "ok"]
