import sqlite3

import pytest
from fastapi.testclient import TestClient

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import SchemaOutdatedError, init_db, make_engine


def test_fresh_database_passes_the_schema_check(tmp_path):
    init_db(make_engine(Settings(data_dir=tmp_path)))
    init_db(make_engine(Settings(data_dir=tmp_path)))  # idempotent on an existing, current database


def test_outdated_database_fails_with_a_clear_message(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    con = sqlite3.connect(settings.db_path)
    con.execute("create table source (id integer primary key, predmet_id integer, path text)")  # old shape
    con.commit()
    con.close()
    with pytest.raises(SchemaOutdatedError, match=r"source: .*sha256.*delete data/scriptmaker.db"):
        init_db(make_engine(settings))


def test_app_refuses_to_start_on_an_outdated_database(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    con = sqlite3.connect(settings.db_path)
    con.execute("create table setting (key text primary key)")  # lacks "value"
    con.commit()
    con.close()
    with pytest.raises(SchemaOutdatedError), TestClient(create_app(settings), base_url="http://127.0.0.1:8765"):
        pass
