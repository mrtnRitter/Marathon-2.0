import hashlib

from sqlalchemy import text

from marathon.server.auth.sessions import create_session, delete_session, resolve_session


def _insert_user(connection):
    connection.execute(
        text("INSERT INTO users (id, email, display_name, role) VALUES ('session-user', 'session@looks.film', 'Session User', 'user')")
    )


def test_create_session_hashes_token_limits_user_agent_and_resolves(db_engine):
    with db_engine.begin() as connection:
        _insert_user(connection)
        token = create_session(connection, "session-user", "x" * 350, 180)
        row = connection.execute(
            text("SELECT id, token_hash, user_agent FROM sessions WHERE user_id = 'session-user'")
        ).mappings().one()
        resolved = resolve_session(connection, token, 180)

    assert row["id"]
    assert row["token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in row["token_hash"]
    assert len(row["user_agent"]) == 300
    assert resolved is not None
    user, renewed = resolved
    assert (user.id, user.email, user.display_name, user.role) == (
        "session-user", "session@looks.film", "Session User", "user"
    )
    assert user.session_id == row["id"]
    assert user.is_admin is False
    assert renewed is False


def test_expired_session_does_not_resolve(db_engine):
    with db_engine.begin() as connection:
        _insert_user(connection)
        token = create_session(connection, "session-user", "test", 180)
        connection.execute(text("UPDATE sessions SET expires_at = now() WHERE user_id = 'session-user'"))
        resolved = resolve_session(connection, token, 180)

    assert resolved is None


def test_session_renews_below_eighty_percent_remaining(db_engine):
    with db_engine.begin() as connection:
        _insert_user(connection)
        token = create_session(connection, "session-user", "test", 180)
        connection.execute(
            text("UPDATE sessions SET expires_at = now() + interval '100 days' WHERE user_id = 'session-user'")
        )
        resolved = resolve_session(connection, token, 180)
        row = connection.execute(
            text(
                "SELECT expires_at > now() + interval '179 days' "
                "AND expires_at < now() + interval '181 days' AS expires_near_180, "
                "last_seen_at > now() - interval '1 minute' AS seen_recently "
                "FROM sessions WHERE user_id = 'session-user'"
            )
        ).mappings().one()

    assert resolved is not None and resolved[1] is True
    assert row["expires_near_180"] is True
    assert row["seen_recently"] is True


def test_session_does_not_renew_at_or_above_eighty_percent_remaining(db_engine):
    with db_engine.begin() as connection:
        _insert_user(connection)
        token = create_session(connection, "session-user", "test", 180)
        connection.execute(
            text("UPDATE sessions SET expires_at = now() + interval '170 days' WHERE user_id = 'session-user'")
        )
        resolved = resolve_session(connection, token, 180)
        unchanged = connection.execute(
            text(
                "SELECT expires_at > now() + interval '169 days' "
                "AND expires_at < now() + interval '171 days' "
                "FROM sessions WHERE user_id = 'session-user'"
            )
        ).scalar_one()

    assert resolved is not None and resolved[1] is False
    assert unchanged is True


def test_delete_session_removes_row(db_engine):
    with db_engine.begin() as connection:
        _insert_user(connection)
        token = create_session(connection, "session-user", "test", 180)
        session_id = connection.execute(
            text("SELECT id FROM sessions WHERE user_id = 'session-user'")
        ).scalar_one()
        delete_session(connection, session_id)
        count = connection.execute(text("SELECT count(*) FROM sessions WHERE user_id = 'session-user'")).scalar_one()

    assert token
    assert count == 0
