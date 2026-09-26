import os

import pytest

from app.db.session import check_database

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL integration URL is not set"
)


def test_postgres_connection_is_healthy() -> None:
    assert check_database()
