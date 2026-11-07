import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from scriptmaker.app import create_app
from scriptmaker.config import Settings

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "localhost", "[::1]:8765"])
def test_local_hosts_allowed(client, host):
    assert client.get("/health", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.test", "evil.test:8765", "127.0.0.1.evil.test", "", "0.0.0.0:8765"])
def test_foreign_host_rejected(client, host):
    r = client.get("/health", headers={"host": host})
    assert r.status_code == 403


def test_cross_origin_post_rejected_but_own_origin_reaches_routing(client):
    # /health has no POST handler: a request that passes the guard gets 405, one that does not gets 403
    assert client.post("/health", headers={"origin": "https://evil.test"}).status_code == 403
    assert client.post("/health", headers={"origin": "null"}).status_code == 403
    assert client.post("/health", headers={"origin": LOCAL}).status_code == 405  # the app's own origin
    assert client.post("/health").status_code == 405  # curl-style, no Origin


def test_other_local_ports_are_cross_origin(client):
    # a dev server or Docker's backend on another port of the same machine is NOT this app (audit A-01)
    for origin in ("http://localhost:3000", "http://127.0.0.1:9999", "http://127.0.0.1:8000", "http://localhost:8765"):
        assert client.post("/health", headers={"origin": origin}).status_code == 403, origin


def test_pages_render_in_croatian(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'lang="hr"' in home.text and "Moji predmeti" in home.text
    assert "Dizajn tokeni" not in home.text  # the developer page is not offered to users (audit A-08)


def test_assets_served(client):
    css = client.get("/static/tokens.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert "--prog-1" in css.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/../app.py").status_code in {400, 404}


def test_openapi_surface_is_off(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_database_created_with_all_tables(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings), base_url=LOCAL) as c:
        assert c.get("/health").status_code == 200
    assert settings.db_path.exists()
    from sqlmodel import create_engine

    tables = set(inspect(create_engine(settings.db_url)).get_table_names())
    assert {"predmet", "unit", "source", "docpage", "question", "answer", "progress",
            "studysession", "job", "setting"} <= tables


@pytest.fixture
def dev_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, dev=True)), base_url=LOCAL) as c:
        yield c


def test_tokens_page_exists_only_in_dev_mode(client, dev_client):
    assert client.get("/design/tokens").status_code == 404
    page = dev_client.get("/design/tokens")
    assert page.status_code == 200
    assert page.text.count('role="progressbar"') == 5 * 4  # five bars in each of four tiles
    assert "Dizajn tokeni" in dev_client.get("/").text  # nav link appears with the page


HTML = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def test_unknown_page_in_a_browser_gets_a_croatian_html_page(client):
    r = client.get("/nope", headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "Stranica nije pronađena" in r.text and 'href="/"' in r.text and 'lang="hr"' in r.text


def test_unknown_page_for_programs_stays_json(client):
    r = client.get("/nope")  # no Accept header preference: curl style
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.get("/nope", headers={"accept": "application/json"})
    assert r.json() == {"detail": "Not Found"}
    r = client.get("/nope", headers={"accept": "application/json, text/html"})
    assert r.headers["content-type"].startswith("application/json")


def test_missing_static_file_and_wrong_method_also_get_friendly_pages(client):
    assert "Stranica nije pronađena" in client.get("/static/does-not-exist.css", headers=HTML).text
    wrong = client.post("/health", headers=HTML)
    assert wrong.status_code == 405 and "Radnja nije dopuštena" in wrong.text


def test_unexpected_error_shows_a_calm_page_and_does_not_leak_details(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as c:
        page = c.get("/boom", headers=HTML)
        assert page.status_code == 500 and "Nešto je pošlo po zlu" in page.text
        assert "secret internal detail" not in page.text and "Traceback" not in page.text
        api = c.get("/boom")
        assert api.status_code == 500 and api.json() == {"detail": "Internal Server Error"}


def test_error_page_text_is_escaped_and_uses_the_shell(client):
    r = client.get("/<script>alert(1)</script>", headers=HTML)
    assert "<script>alert(1)" not in r.text
