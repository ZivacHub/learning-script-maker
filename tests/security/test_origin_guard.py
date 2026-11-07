"""Origin / fetch-metadata checks on state-changing requests (audit A-01)."""

import pytest
from fastapi.testclient import TestClient

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.security import is_same_origin, parse_host, parse_origin

LOCAL = "http://127.0.0.1:8765"
UNSAFE = ["post", "put", "patch", "delete"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url=LOCAL) as c:
        yield c


def send(client, method, **headers):
    # /health has no handler for these methods: 405 = passed the guard, 403 = stopped by the guard
    return client.request(method, "/health", headers=headers).status_code


@pytest.mark.parametrize("method", UNSAFE)
@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.test",
        "http://evil.test:8765",
        "http://localhost:3000",  # another local web app (audit A-01, reproduced)
        "http://127.0.0.1:9999",
        "http://127.0.0.1:8000",  # Docker Desktop's backend on this PC
        "http://localhost:8765",  # same port but a different host name than the page was loaded from
        "https://127.0.0.1:8765",  # other scheme
        "http://127.0.0.1",  # default port 80
        "null",
        "",
        "not a url",
        "http://127.0.0.1:8765/path",
    ],
)
def test_foreign_origins_are_blocked_on_every_unsafe_method(client, method, origin):
    assert send(client, method, origin=origin) == 403


@pytest.mark.parametrize("method", UNSAFE)
def test_own_origin_and_no_origin_pass_the_guard(client, method):
    assert send(client, method, origin=LOCAL) == 405
    assert send(client, method) == 405


@pytest.mark.parametrize("method", UNSAFE)
@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site", "weird"])
def test_only_same_origin_or_none_fetch_metadata_passes(client, method, site):
    assert send(client, method, **{"sec-fetch-site": site}) == 403


@pytest.mark.parametrize("site", ["same-origin", "none", "SAME-ORIGIN"])
def test_same_origin_fetch_metadata_passes(client, site):
    assert send(client, "post", **{"sec-fetch-site": site, "origin": LOCAL}) == 405


@pytest.mark.parametrize("method", ["get", "head", "options"])
def test_safe_methods_ignore_origin_checks(client, method):
    # reading is allowed (the Host check already stops DNS rebinding); nothing changes state
    assert send(client, method, origin="https://evil.test") in {200, 405}


def test_origin_must_match_the_host_the_page_was_loaded_from(client):
    # page loaded via localhost:8765 -> its own origin is http://localhost:8765
    assert send(client, "post", host="localhost:8765", origin="http://localhost:8765") == 405
    assert send(client, "post", host="localhost:8765", origin=LOCAL) == 403
    assert send(client, "post", host="[::1]:8765", origin="http://[::1]:8765") == 405


def test_unit_helpers():
    assert parse_origin("http://LOCALHOST:8765") == ("http", "localhost", 8765)
    assert parse_origin("https://x.test") == ("https", "x.test", 443)
    assert parse_origin("http://[::1]:8765") == ("http", "[::1]", 8765)
    assert parse_origin("null") is None and parse_origin("ftp://x") is None and parse_origin("http://x:99999") is None
    assert is_same_origin("http://127.0.0.1:8765", parse_host("127.0.0.1:8765"))
    assert is_same_origin("http://127.0.0.1", parse_host("127.0.0.1"))
    assert not is_same_origin("http://127.0.0.1:8765", parse_host("127.0.0.1"))
