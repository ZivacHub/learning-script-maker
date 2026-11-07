"""Step 5.2: the import wizard (choose -> confirm table -> background reading -> result)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import SourceRole
from scriptmaker.db import Job, Predmet, Setting, Source, Unit
from scriptmaker.ingest import build_plan
from scriptmaker.web.importing import (
    ImportBusyError,
    JobProgress,
    PendingImports,
    apply_form,
    group_files,
    mark_interrupted_jobs,
    remembered_folder_roles,
)

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data"))
    app.state.import_runner.chain = []  # only text files in these tests, so no reader is needed
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL, follow_redirects=False) as c:
        yield c


@pytest.fixture
def material(tmp_path) -> Path:
    root = tmp_path / "Matematika_usmeni"
    (root / "Pitanja").mkdir(parents=True)
    (root / "Pitanja" / "popis.txt").write_text("1. a) Definirajte izometriju.\n", encoding="utf-8")
    unit = root / "Materijali" / "1. Izometrija"
    unit.mkdir(parents=True)
    (unit / "a.txt").write_text("Izometrija je preslikavanje koje cuva udaljenost.", encoding="utf-8")
    (unit / "b.txt").write_text("Rotacija je izometrija.", encoding="utf-8")
    (unit / "kopija.txt").write_text("Rotacija je izometrija.", encoding="utf-8")  # same content as b.txt
    (root / "Ana bilješke").mkdir()
    (root / "Ana bilješke" / "n.txt").write_text("Moje bilješke o translaciji.", encoding="utf-8")
    (root / "stara.rar").write_bytes(b"Rar!")
    return root


def pick(app, client, folder: Path) -> str:
    """Walk the first step with a fixed folder instead of the Windows window; returns the plan token."""
    app.state.choose_folder = lambda: folder
    r = client.post("/uvoz/mapa")
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[1]


def wait_for(app, location: str) -> JobProgress:
    job_id = int(location.rsplit("/", 1)[1])
    app.state.import_runner.join(job_id, 60)
    return app.state.import_runner.get(app.state.engine, job_id)


# --- choosing ---------------------------------------------------------------------------------------------------


def test_start_page_offers_folder_zip_and_no_shortcut_block_without_shortcuts(client):
    page = client.get("/uvoz", headers=HTML)
    assert page.status_code == 200
    assert "Odaberi mapu" in page.text and "Učitaj zip" in page.text and "Brzo učitaj" not in page.text
    assert "/static/vendor/htmx.min.js" in page.text
    assert client.get("/static/vendor/htmx.min.js").status_code == 200


def test_folder_window_answer_leads_to_the_confirm_page(app, client, material):
    token = pick(app, client, material)
    page = client.get(f"/uvoz/plan/{token}")
    assert page.status_code == 200
    assert "Provjeri uvoz" in page.text and 'value="Matematika usmeni"' in page.text
    assert "Izometrija" in page.text and "popis.txt" in page.text and "a.txt" in page.text


def test_button_through_htmx_gets_a_redirect_header_and_a_closed_window_a_calm_message(app, client, material):
    app.state.choose_folder = lambda: material
    r = client.post("/uvoz/mapa", headers={"hx-request": "true"})
    assert r.status_code == 200 and r.headers["hx-redirect"].startswith("/uvoz/plan/")
    app.state.choose_folder = lambda: None
    r = client.post("/uvoz/mapa", headers={"hx-request": "true"})
    assert r.status_code == 200 and "Mapa nije odabrana" in r.text
    assert "Mapa nije odabrana" in client.post("/uvoz/mapa").text  # without HTMX: the whole page


def test_shortcut_opens_the_confirm_page_and_a_missing_folder_is_explained(app, client, material, tmp_path):
    from scriptmaker.ingest.persist import add_shortcut

    with Session(app.state.engine) as s:
        add_shortcut(s, "Matematika", material)
    page = client.get("/uvoz").text
    assert "Brzo učitaj" in page and "Matematika" in page and "precac=0" in page
    r = client.get("/uvoz?precac=0")
    assert r.status_code == 303 and r.headers["location"].startswith("/uvoz/plan/")
    assert client.get("/uvoz?precac=7").status_code == 404
    gone = tmp_path / "gone"
    gone.mkdir()
    with Session(app.state.engine) as s:
        add_shortcut(s, "Nestala", gone)
    gone.rmdir()
    r = client.get("/uvoz?precac=1")
    assert r.status_code == 400 and "Nestala" in r.text and "Provjeri postoji li još" in r.text


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buffer.getvalue()


def test_zip_upload_is_unpacked_safely_and_named_from_the_file(client):
    data = make_zip({"Fizika/1. Mehanika/a.txt": b"Sila je vektor.", "Fizika/Pitanja/p.txt": b"1. Sto je sila?"})
    r = client.post("/uvoz/zip", files={"file": ("Fizika_skripta.zip", data, "application/zip")})
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Mehanika" in page and "a.txt" in page


def test_unsafe_or_wrong_zip_is_refused_with_a_plain_message(client):
    evil = make_zip({"../izvan.txt": b"x"})
    r = client.post("/uvoz/zip", files={"file": ("evil.zip", evil, "application/zip")})
    assert r.status_code == 400 and "nije siguran" in r.text
    r = client.post("/uvoz/zip", files={"file": ("slika.png", b"x", "image/png")})
    assert r.status_code == 400 and ".zip" in r.text
    r = client.post("/uvoz/zip", files={"file": ("lose.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


def test_unknown_plan_token_gets_a_friendly_page(client):
    r = client.get("/uvoz/plan/0123456789abcdef")
    assert r.status_code == 404 and "Odaberi mapu ponovno" in r.text
    assert client.post("/uvoz/plan/0123456789abcdef/potvrdi").status_code == 404


def test_posts_from_another_site_are_refused(client):
    assert client.post("/uvoz/mapa", headers={"origin": "https://evil.test"}).status_code == 403
    assert client.post("/uvoz/zip", headers={"origin": "https://evil.test"}).status_code == 403


# --- the confirm table ------------------------------------------------------------------------------------------


def test_table_groups_files_by_folder_and_gives_each_file_its_own_role(app, client, material):
    page = client.get(f"/uvoz/plan/{pick(app, client, material)}").text
    flat = " ".join(page.split())
    assert page.count("data-file-role") == 4  # popis, a, b, n: the copy and the .rar are not imported
    assert page.count("data-group-role") == 3  # Pitanja, Materijali/1. Izometrija, Ana bilješke
    assert "Provjeri ulogu" in page  # the unknown folder is only a guess
    assert "Preskočeno (2)" in page and "kopija.txt" in page and "isti sadržaj kao druga datoteka" in flat
    assert "stara.rar" in page and "RAR nije podržan" in page
    assert all(label in page for label in ("Pitanja", "Gradivo", "Primjeri ispita", "Bilješke"))


def test_names_are_escaped_in_the_table(app, client, tmp_path):
    root = tmp_path / "x"  # Windows does not allow < > in names, so the check uses an ampersand and an apostrophe
    (root / "R&D mapa").mkdir(parents=True)
    (root / "R&D mapa" / "Ana's &lt; datoteka.txt").write_text("tekst", encoding="utf-8")
    page = client.get(f"/uvoz/plan/{pick(app, client, root)}").text
    assert "R&amp;D mapa" in page and "R&D mapa" not in page
    assert "Ana&#39;s &amp;lt; datoteka.txt" in page


def test_apply_form_changes_name_titles_and_roles_and_ignores_nonsense(material):
    plan = build_plan(material)
    pending = PendingImports().add(plan)
    index = {f.rel_path: i for i, f in enumerate(plan.files)}
    form = {
        "name": "  Moj   predmet ",
        "unit-1": "Izometrije",
        f"role-{index['Pitanja/popis.txt']}": "exam_samples",
        f"role-{index['Materijali/1. Izometrija/a.txt']}": "not-a-role",
        f"role-{index['Materijali/1. Izometrija/kopija.txt']}": "questions",  # a skipped duplicate: stays skipped
    }
    done = apply_form(pending, form)
    by = {f.rel_path: f for f in done.files}
    assert done.predmet_name == "Moj predmet" and done.units[0].title == "Izometrije"
    assert by["Pitanja/popis.txt"].role is SourceRole.EXAM_SAMPLES and by["Pitanja/popis.txt"].role_source == "user"
    assert by["Materijali/1. Izometrija/a.txt"].role is SourceRole.MATERIALS
    assert by["Materijali/1. Izometrija/kopija.txt"].action == "skip"
    assert not any(f.needs_confirmation for f in done.files)  # confirming the table confirms the guesses
    assert plan.predmet_name != "Moj predmet"  # the original plan is not changed


def test_a_folder_given_one_new_role_is_remembered_but_a_mixed_one_is_not(material):
    plan = build_plan(material)
    pending = PendingImports().add(plan)
    index = {f.rel_path: i for i, f in enumerate(plan.files)}
    notes = apply_form(pending, {f"role-{index['Ana bilješke/n.txt']}": "materials"})
    assert remembered_folder_roles(pending, notes) == {"Ana bilješke": SourceRole.MATERIALS}
    mixed = apply_form(pending, {f"role-{index['Materijali/1. Izometrija/a.txt']}": "notes"})
    assert "1. Izometrija" not in remembered_folder_roles(pending, mixed)  # b.txt kept its role: mixed
    assert remembered_folder_roles(pending, apply_form(pending, {})) == {}  # nothing changed


def test_groups_have_a_single_role_only_when_all_files_agree(material):
    groups = {g.folder: g for g in group_files(build_plan(material))}
    assert groups["Pitanja"].single_role == "questions" and groups["Ana bilješke"].needs_confirmation
    assert set(groups) == {"Pitanja", "Materijali/1. Izometrija", "Ana bilješke"}


# --- reading ----------------------------------------------------------------------------------------------------


def confirm(client, token: str, form: dict | None = None):
    return client.post(f"/uvoz/plan/{token}/potvrdi", data=form or {})


def test_confirmed_import_is_saved_read_and_counted(app, client, material):
    plan = build_plan(material)
    index = {f.rel_path: i for i, f in enumerate(plan.files)}
    token = pick(app, client, material)
    r = confirm(client, token, {"name": "Matematika", f"role-{index['Ana bilješke/n.txt']}": "materials"})
    assert r.status_code == 303 and r.headers["location"].startswith("/uvoz/posao/")
    job = wait_for(app, r.headers["location"])
    assert job.state == "done" and job.summary["files"] == 4 and job.summary["errors"] == 0
    assert job.summary["pages"] == 4

    with Session(app.state.engine) as s:
        predmet = s.exec(select(Predmet)).one()
        roles = {Path(src.path).name: src.role for src in s.exec(select(Source))}
        assert predmet.name == "Matematika" and s.exec(select(Unit)).one().title == "Izometrija"
        assert roles["n.txt"] == "materials" and roles["popis.txt"] == "questions" and "kopija.txt" not in roles
        assert s.get(Setting, f"review_count:{predmet.id}").value == "0"
        assert s.exec(select(Job)).one().state == "done"
        index_file = app.state.settings.data_dir / "docs" / f"index-p{predmet.id}.json"
    saved = {Path(e["rel_path"]).name: e["role"] for e in json.loads(index_file.read_text(encoding="utf-8"))["files"]}
    assert saved["n.txt"] == "materials"  # the read result carries the role Ivan chose
    assert not (app.state.settings.data_dir / "docs" / "index.json").exists()  # the command-line index is left alone


def test_progress_page_polls_while_running_and_stops_when_done(app, client, material):
    r = confirm(client, pick(app, client, material))
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    app.state.import_runner.join(job_id, 60)
    done = client.get(r.headers["location"]).text
    assert "Gotovo" in done and "Za provjeru: 0" in done and "hx-trigger" not in done
    fragment = client.get(f"/uvoz/posao/{job_id}/stanje").text
    assert "Gotovo" in fragment and "<html" not in fragment

    running = JobProgress(99, 1, "Fizika", total_files=10, done_files=4, current="a/b.pdf")
    app.state.import_runner._jobs[99] = running
    text = client.get("/uvoz/posao/99/stanje").text
    assert 'hx-trigger="every 1s"' in text and "a/b.pdf" in text and 'aria-valuenow="40"' in text
    assert "<b>4</b> od <b>10</b>" in text.replace("\n", " ") and "Prekini čitanje" in text


def test_only_one_import_at_a_time(app, client, material):
    app.state.import_runner._jobs[99] = JobProgress(99, 1, "Drugi", total_files=3)
    token = pick(app, client, material)
    assert "Jedan uvoz se još čita" in client.get(f"/uvoz/plan/{token}").text
    r = confirm(client, token)
    assert r.status_code == 409 and "Pričekaj" in r.text
    with pytest.raises(ImportBusyError):
        app.state.import_runner.start(app.state.engine, app.state.settings, build_plan(material), 1)


def test_cancel_marks_the_job_and_the_page_says_so(app, client):
    runner = app.state.import_runner
    runner._jobs[99] = JobProgress(99, 1, "Fizika", total_files=10, done_files=2)
    text = client.post("/uvoz/posao/99/prekini", headers={"hx-request": "true"}).text
    assert runner._jobs[99].cancel and "prekidam" in text
    runner._jobs[99].state = "canceled"
    assert "Prekinuto" in client.get("/uvoz/posao/99").text
    assert client.post("/uvoz/posao/12345/prekini").status_code == 404


def test_failed_reading_shows_a_calm_message(app, client, material, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret disk detail")

    monkeypatch.setattr("scriptmaker.web.importing.read_folder", boom)
    r = confirm(client, pick(app, client, material))
    job = wait_for(app, r.headers["location"])
    page = client.get(r.headers["location"]).text
    assert job.state == "failed" and "Čitanje nije uspjelo" in page and "secret disk detail" not in page


def test_files_that_cannot_be_read_are_listed_not_hidden(app, client, tmp_path):
    root = tmp_path / "Loše"
    root.mkdir()
    (root / "dobra.txt").write_text("tekst", encoding="utf-8")
    (root / "kriva.docx").write_bytes(b"this is not a word file")
    r = confirm(client, pick(app, client, root))
    job = wait_for(app, r.headers["location"])
    page = client.get(r.headers["location"]).text
    assert job.state == "done" and job.summary["errors"] == 1
    assert "kriva.docx" in page and "nije pročitana" in page


def test_a_job_cut_off_by_closing_the_app_is_marked_failed_at_the_next_start(app):
    with TestClient(app, base_url=LOCAL):
        pass
    with Session(app.state.engine) as s:
        s.add(Job(kind="import", state="running", log=json.dumps({"predmet_id": 3, "name": "Fizika"})))
        s.commit()
    assert mark_interrupted_jobs(app.state.engine) == 1
    job = app.state.import_runner.get(app.state.engine, 1)
    assert job.state == "failed" and "prekinut" in job.error and job.name == "Fizika"


def test_review_count_appears_on_the_dashboard(app, client):
    with Session(app.state.engine) as s:
        p = Predmet(name="Fizika")
        s.add(p)
        s.flush()
        s.add(Setting(key=f"review_count:{p.id}", value="41"))
        s.commit()
    page = client.get("/").text
    assert re.search(r"Za provjeru: 41</b>\s*stranica", page)
