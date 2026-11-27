"""Tests for global use access to newly installed standard macros."""

from sqlalchemy import text

from marathon.modules import load_catalog
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.macro_writes import ensure_standard_macros
from marathon.server.db.object_access import object_access


def _install_standard_macros(connection) -> int:
    sync_module_types(connection, load_catalog())
    return ensure_standard_macros(connection)


def test_new_standard_macros_are_shared_for_use(db_engine):
    user = AuthUser("regular-user", "user@example.test", "Regular User", "user", "test-session")
    with db_engine.begin() as connection:
        created = _install_standard_macros(connection)
        macro_ids = list(connection.execute(text("SELECT id FROM macros ORDER BY id")).scalars())
        shares = list(connection.execute(text(
            "SELECT object_id, user_id, access FROM object_shares "
            "WHERE object_type = 'macro' ORDER BY object_id"
        )).all())

        assert created == len(macro_ids) > 0
        assert shares == [(macro_id, None, "use") for macro_id in macro_ids]
        assert all(object_access(connection, user, "macro", macro_id) == "use" for macro_id in macro_ids)


def test_second_install_does_not_duplicate_standard_macro_shares(db_engine):
    with db_engine.begin() as connection:
        first_created = _install_standard_macros(connection)
        second_created = ensure_standard_macros(connection)
        macro_count = connection.execute(text("SELECT count(*) FROM macros")).scalar_one()
        share_count = connection.execute(text(
            "SELECT count(*) FROM object_shares WHERE object_type = 'macro'"
        )).scalar_one()

        assert first_created > 0
        assert second_created == 0
        assert share_count == macro_count == first_created


def test_reinstall_does_not_restore_removed_standard_macro_share(db_engine):
    with db_engine.begin() as connection:
        created = _install_standard_macros(connection)
        macro_id = connection.execute(text("SELECT id FROM macros ORDER BY id LIMIT 1")).scalar_one()
        connection.execute(text(
            "DELETE FROM object_shares WHERE object_type = 'macro' AND object_id = :macro_id "
            "AND user_id IS NULL"
        ), {"macro_id": macro_id})

        assert created > 0
        assert ensure_standard_macros(connection) == 0
        remaining_shares = connection.execute(text(
            "SELECT count(*) FROM object_shares WHERE object_type = 'macro' AND object_id = :macro_id"
        ), {"macro_id": macro_id}).scalar_one()
        assert remaining_shares == 0
