import pytest

from readlater.store.db import DB


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()
