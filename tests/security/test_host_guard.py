"""Host header validation (audit A-02): only a well-formed `name[:port]` for a local name passes."""

import pytest
from fastapi.testclient import TestClient

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.security import hostname_of, parse_host

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url=LOCAL) as c:
        yield c


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1", ("127.0.0.1", None)),
        ("127.0.0.1:8765", ("127.0.0.1", 8765)),
        ("LOCALHOST:8765", ("localhost", 8765)),
        ("localhost", ("localhost", None)),
        ("[::1]:8765", ("[::1]", 8765)),
        ("[::1]", ("[::1]", None)),
        ("  localhost:80  ", ("localhost", 80)),
        ("evil.test:443", ("evil.test", 443)),  # well-formed, rejected later by the allow-list
    ],
)
def test_parse_host_accepts_well_formed_values(header, expected):
    assert parse_host(header) == expected


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "127.0.0.1:8799, evil.test",  # reproduced in the audit: junk after the port
        "127.0.0.1:abc",  # reproduced in the audit: non-numeric port
        "127.0.0.1:",
        "127.0.0.1:0",
        "127.0.0.1:70000",
        "127.0.0.1:-1",
        "a@127.0.0.1",
        "127.0.0.1 evil.test",
        "127.0.0.1/path",
        r"127.0.0.1\evil",
        "127.0.0.1:8765:9",
        "[::1",
        "[::1]x",
        "[zz]:80",
        "local host",
        "loc\talhost",
    ],
)
def test_parse_host_rejects_malformed_values(header):
    assert parse_host(header) is None
    assert hostname_of(header) is None


@pytest.mark.parametrize(
    "header",
    [
        "127.0.0.1:8765, evil.test",
        "127.0.0.1:abc",
        "a@127.0.0.1",
        "127.0.0.1:99999",
        "evil.test",
        "localhost.evil.test",
    ],
)
def test_app_refuses_bad_hosts_with_403(client, header):
    assert client.get("/health", headers={"host": header}).status_code == 403


@pytest.mark.parametrize("header", ["127.0.0.1:8765", "localhost:8765", "localhost", "[::1]:8765", "LOCALHOST"])
def test_app_accepts_good_local_hosts(client, header):
    assert client.get("/health", headers={"host": header}).status_code == 200
