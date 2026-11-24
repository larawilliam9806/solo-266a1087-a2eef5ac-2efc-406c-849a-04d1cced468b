import os
import tempfile

import pytest

# Isolated SQLite DB before the app module constructs its singleton.
_TMP = tempfile.mkdtemp(prefix="bangdan-tests-")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, storage  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_config():
    storage.reset_empty()
    yield
