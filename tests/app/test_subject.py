"""Step 5.3: the subject page (units with files and page states, reliability, "Kako želim učiti", build button)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import (
    Block,
    BlockType,
    DocModel,
    Focus,
    Page,
    Preferences,
    Script,
    SourceInfo,
    SourceRole,
    Strictness,
    Template,
    Theme,
)
from scriptmaker.db import Predmet, Source, Unit, init_db
from scriptmaker.extract.folder import doc_path, source_id_for
from scriptmaker.render.build import RenderOptions, render_script
from scriptmaker.web.subject import classify_pages, load_preferences, load_sources, save_preferences

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}
HX = {"hx-request": "true"}
FIXTURES = Path(__file__).parents[1] / "fixtures"

SHA_SLIDES = "a" * 64
SHA_NOTES = "b" * 64
SHA_QUESTIONS = "c" * 64
SHA_TEXT = "d" * 64


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)  # tests that never start the app still need the tables
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def block(i: int, confidence: float | None, engine: str = "rapidocr", edited: bool = False) -> Block:
    return Block(
        id=f"b{i}", type=BlockType.PARAGRAPH, text=f"redak {i}", engine=engine, confidence=confidence,
        edited_by_user=edited,
    )


def write_doc(data_dir: Path, sha: str, name: str, role: SourceRole, pages: list[Page]) -> None:
    source = SourceInfo(id=source_id_for(sha), path=name, sha256=sha, role=role)
    target = doc_path(data_dir, source.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DocModel(source=source, pages=pages).model_dump_json(), encoding="utf-8")


def add_subject(app, data_dir: Path, name: str = "Matematika") -> dict[str, int]:
    """Two units; slides (unit 1, four pages), handwritten photo in units 1 and 2, a question list with no unit and
    no reading result, and a text file nobody read."""
    write_doc(
        data_dir, SHA_SLIDES, "slides.pdf", SourceRole.MATERIALS,
        [
            Page(n=1, needs_ocr=False, blocks=[block(1, None, "textlayer")]),
            Page(n=2, needs_ocr=True, blocks=[block(2, 0.96), block(3, 0.95)]),
            Page(n=3, needs_ocr=True, blocks=[block(4, 0.3), block(5, 0.4)]),
            Page(n=4, needs_ocr=True, blocks=[]),
        ],
    )
    write_doc(
        data_dir, SHA_NOTES, "bilješka.jpg", SourceRole.NOTES,
        [Page(n=1, needs_ocr=True, blocks=[block(6, 0.9)])],
    )
    ids: dict[str, int] = {}
    with Session(app.state.engine) as s:
        p = Predmet(name=name)
        s.add(p)
        s.flush()
        u1 = Unit(predmet_id=p.id, no=1, title="Izometrija")
        u2 = Unit(predmet_id=p.id, no=2, title="Sličnost")
        s.add_all([u1, u2])
        s.flush()
        rows = {
            "slides": Source(predmet_id=p.id, unit_id=u1.id, unit_nos_json="[1]",
                             path="C:/gradivo/1. Izometrija/slides.pdf",
                             sha256=SHA_SLIDES, role="materials", reliability=4),
            "notes": Source(predmet_id=p.id, unit_id=u1.id, unit_nos_json="[1, 2]", path="C:/gradivo/Ana/bilješka.jpg",
                            sha256=SHA_NOTES, role="notes"),
            "questions": Source(predmet_id=p.id, unit_nos_json="[]", path="C:/gradivo/Pitanja/popis.pdf",
                                sha256=SHA_QUESTIONS, role="questions", reliability=5),
        }
        s.add_all(rows.values())
        s.commit()
        ids = {key: row.id for key, row in rows.items()}
        ids["predmet"] = p.id
    return ids


# --- reading results of pages ----------------------------------------------------------------------------------------


def test_every_page_gets_one_of_four_states(app, tmp_path):
    add_subject(app, tmp_path)
    pages = classify_pages(tmp_path, SHA_SLIDES)
    assert [(p.n, p.state) for p in pages] == [(1, "text"), (2, "ocr"), (3, "review"), (4, "review")]
    assert "low confidence" in pages[2].reason and "no text found" in pages[3].reason
    notes = classify_pages(tmp_path, SHA_NOTES)
    assert [p.state for p in notes] == ["review"]  # a photo in the notes role is always a draft until a person looks


def test_a_page_a_person_corrected_counts_as_corrected(tmp_path):
    write_doc(
        tmp_path, SHA_TEXT, "x.jpg", SourceRole.NOTES,
        [Page(n=1, needs_ocr=True, blocks=[block(1, 0.2, edited=True), block(2, 0.2, "manual", edited=True)])],
    )
    assert [p.state for p in classify_pages(tmp_path, SHA_TEXT)] == ["edited"]


def test_a_file_without_a_result_has_no_pages_and_a_changed_result_is_read_again(tmp_path):
    assert classify_pages(tmp_path, SHA_TEXT) is None
    one = [Page(n=1, needs_ocr=False, blocks=[block(1, None, "textlayer")])]
    write_doc(tmp_path, SHA_TEXT, "x.pdf", SourceRole.MATERIALS, one)
    assert len(classify_pages(tmp_path, SHA_TEXT)) == 1
    write_doc(
        tmp_path, SHA_TEXT, "x.pdf", SourceRole.MATERIALS,
        [Page(n=n, needs_ocr=False, blocks=[block(n, None, "textlayer")]) for n in (1, 2)],
    )
    doc_path(tmp_path, source_id_for(SHA_TEXT)).touch()
    assert len(classify_pages(tmp_path, SHA_TEXT)) == 2


def test_units_hold_their_files_and_a_file_of_two_units_shows_in_both(app, tmp_path):
    add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        data = load_sources(s, tmp_path, 1)
    assert [(u.no, [f.name for f in u.sources]) for u in data.units] == [
        (1, ["bilješka.jpg", "slides.pdf"]),
        (2, ["bilješka.jpg"]),
    ]
    assert [f.name for f in data.loose] == ["popis.pdf"] and not data.loose[0].read
    slides = data.units[0].sources[1]
    assert (slides.role_label, slides.reliability, slides.counts["review"]) == ("Gradivo", 4, 2)
    assert data.units[0].review_count == 3  # two slide pages plus the photo


# --- the page --------------------------------------------------------------------------------------------------------


def test_unknown_predmet_gets_the_calm_not_found_page(client):
    page = client.get("/predmet/999", headers=HTML)
    assert page.status_code == 404 and "Stranica nije pronađena" in page.text
    assert client.get("/predmet/999/cjeline", headers=HTML).status_code == 404


def test_page_shows_name_counts_and_the_build_link(app, client, tmp_path):
    ids = add_subject(app, tmp_path, "Matematika <b>1</b>")
    page = client.get(f"/predmet/{ids['predmet']}", headers=HTML)
    assert page.status_code == 200
    text = page.text
    assert "Matematika &lt;b&gt;1&lt;/b&gt;" in text and "<b>1</b></h1>" not in text  # names are escaped
    assert "2 cjeline, 0 pitanja" in " ".join(text.split())
    assert 'href="/"' in text  # the way back
    assert f'href="/predmet/{ids["predmet"]}/skripta">Izradi skriptu' in text  # leads to the build page (5.5)
    assert "Skripta još nije izrađena" in text
    assert f'hx-get="/predmet/{ids["predmet"]}/cjeline"' in text  # the list loads after the page appears
    assert 'id="units-skeleton"' in text  # and has a loading shape


def test_units_partial_lists_files_roles_stars_and_page_states(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    part = client.get(f"/predmet/{ids['predmet']}/cjeline", headers=HTML)
    assert part.status_code == 200
    text = " ".join(part.text.split())
    for expected in ("Izometrija", "Sličnost", "slides.pdf", "bilješka.jpg", "Datoteke bez cjeline", "popis.pdf"):
        assert expected in text
    assert "4 stranice: 1 tekst iz datoteke, 1 pročitano (OCR), 2 za provjeru" in text
    assert "Nije pročitano" in text  # the question list has no reading result
    assert "Za provjeru: 3" in text  # unit 1 roll-up
    assert text.count('name="reliability"') == 5 * 4  # five stars for each of the four rows (photo is listed twice)
    assert "4 od 5" in text and "3 od 5" in text and "5 od 5" in text
    for role in ("Gradivo", "Bilješke", "Pitanja"):
        assert role in text


def test_page_states_are_not_colour_only(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    text = client.get(f"/predmet/{ids['predmet']}/cjeline").text
    for words in ("tekst iz datoteke", "pročitano OCR-om", "za provjeru", "ispravljeno"):
        assert words in text  # legend in words
    assert text.count('class="pg pg-review"') >= 2 and "pg-mark" in text


def test_units_partial_without_files_explains_how_to_get_some(app, client):
    with Session(app.state.engine) as s:
        p = Predmet(name="Prazno")
        s.add(p)
        s.commit()
        pid = p.id
    text = client.get(f"/predmet/{pid}/cjeline").text
    assert "Ovaj predmet još nema datoteka" in text and 'href="/uvoz"' in text


def test_units_partial_error_state_has_a_retry(app, client, monkeypatch):
    with Session(app.state.engine) as s:
        p = Predmet(name="Matematika")
        s.add(p)
        s.commit()
        pid = p.id

    def boom(*_args, **_kwargs):
        raise RuntimeError("database locked")

    monkeypatch.setattr("scriptmaker.web.routes.subject.load_sources", boom)
    part = client.get(f"/predmet/{pid}/cjeline", headers=HTML)
    assert part.status_code == 503
    assert "Cjeline se nisu učitale" in part.text and "data-retry" in part.text
    assert "database locked" not in part.text  # details go to the log, not to the page


def test_dashboard_card_leads_to_the_subject_page(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    home = client.get("/", headers=HTML).text
    assert f'href="/predmet/{ids["predmet"]}"' in home


# --- reliability -----------------------------------------------------------------------------------------------------


def test_reliability_is_saved_and_shown(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    url = f"/predmet/{ids['predmet']}/izvor/{ids['notes']}/pouzdanost"
    done = client.post(url, data={"reliability": "5"}, headers=HX)
    assert done.status_code == 204
    with Session(app.state.engine) as s:
        assert s.get(Source, ids["notes"]).reliability == 5
    assert "5 od 5" in client.get(f"/predmet/{ids['predmet']}/cjeline").text


def test_reliability_without_the_script_answers_with_a_redirect_back(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    url = f"/predmet/{ids['predmet']}/izvor/{ids['slides']}/pouzdanost"
    done = client.post(url, data={"reliability": "2"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == f"/predmet/{ids['predmet']}"


@pytest.mark.parametrize("value", ["0", "6", "-1", "x", ""])
def test_reliability_outside_one_to_five_is_refused(app, client, tmp_path, value):
    ids = add_subject(app, tmp_path)
    url = f"/predmet/{ids['predmet']}/izvor/{ids['slides']}/pouzdanost"
    assert client.post(url, data={"reliability": value}, headers=HX).status_code == 400
    with Session(app.state.engine) as s:
        assert s.get(Source, ids["slides"]).reliability == 4


def test_reliability_of_a_file_from_another_predmet_is_refused(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    url = f"/predmet/{other_id}/izvor/{ids['slides']}/pouzdanost"
    assert client.post(url, data={"reliability": "1"}, headers=HX).status_code == 404
    with Session(app.state.engine) as s:
        assert s.get(Source, ids["slides"]).reliability == 4


# --- preferences -----------------------------------------------------------------------------------------------------

CHANGES = [
    ({"template": "overview"}, {"template": Template.OVERVIEW}),
    ({"theme": "nacrt"}, {"theme": Theme.NACRT}),
    ({"strictness": "verbatim_only"}, {"strictness": Strictness.VERBATIM_ONLY}),
    ({"strictness": "summary"}, {"strictness": Strictness.SUMMARY}),
    ({"focus": "favorites"}, {"focus": Focus.FAVORITES}),
    ({"focus": "most_asked", "min_weight": "4"}, {"focus": Focus.MOST_ASKED, "min_weight": 4}),
    ({"font_scale": "1.3"}, {"font_scale": 1.3}),
    ({"default_target_passes": "5"}, {"default_target_passes": 5}),
    ({"default_target_passes": "auto"}, {"default_target_passes": None}),
]
BOXES = (
    "include_figures", "include_source_line", "dense", "show_progress", "show_games", "inline_katex",
    "side_menu", "show_mode_cards", "show_tools", "show_legend",
)


def full_form(**changes: str) -> dict[str, str]:
    """What the browser sends: every ticked box is present, every unticked one is missing."""
    base = Preferences()
    form = {
        "template": base.template.value, "theme": base.theme.value, "strictness": base.strictness.value,
        "focus": base.focus.value, "min_weight": "2", "font_scale": "1.0", "default_target_passes": "auto",
    }
    form.update({box: "on" for box in BOXES if getattr(base, box)})
    form.update(changes)
    return form


def test_page_shows_the_saved_choices(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        chosen = Preferences(theme=Theme.NACRT, dense=True, focus=Focus.MOST_ASKED, font_scale=1.15,
                             default_target_passes=3, include_figures=False)
        save_preferences(s, ids["predmet"], chosen)
    text = client.get(f"/predmet/{ids['predmet']}/izgled").text
    assert 'name="theme" value="nacrt" checked' in text and 'name="theme" value="papir" checked' not in text
    assert 'name="dense" checked' in text and 'name="include_figures" checked' not in text
    assert 'name="focus" value="most_asked" checked' in text
    assert '<option value="1.15" selected>' in text and '<option value="3" selected>' in text
    assert "data-when-focus" in text and "hidden" not in text.split("data-when-focus")[1].split(">")[0]


def test_min_weight_choice_is_hidden_until_most_asked_is_chosen(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    text = client.get(f"/predmet/{ids['predmet']}/izgled").text
    assert " hidden" in text.split("data-when-focus")[1].split(">")[0]


@pytest.mark.parametrize(("form", "expected"), CHANGES)
def test_each_choice_is_saved(app, client, tmp_path, form, expected):
    ids = add_subject(app, tmp_path)
    done = client.post(f"/predmet/{ids['predmet']}/postavke", data=full_form(**form), headers=HX)
    assert done.status_code == 204
    with Session(app.state.engine) as s:
        saved = load_preferences(s, ids["predmet"])
    assert saved == Preferences(**expected)
    # and it is what the page shows when it is opened again
    again = client.get(f"/predmet/{ids['predmet']}/izgled").text
    for key, value in expected.items():
        if key in ("template", "theme", "strictness", "focus"):
            assert f'name="{key}" value="{value.value}" checked' in again


@pytest.mark.parametrize("box", BOXES)
def test_each_box_can_be_switched_off_and_on(app, client, tmp_path, box):
    ids = add_subject(app, tmp_path)
    url = f"/predmet/{ids['predmet']}/postavke"
    form = full_form()
    default = getattr(Preferences(), box)
    if default:
        del form[box]
    else:
        form[box] = "on"
    assert client.post(url, data=form, headers=HX).status_code == 204
    with Session(app.state.engine) as s:
        assert getattr(load_preferences(s, ids["predmet"]), box) is (not default)
    assert client.post(url, data=full_form(), headers=HX).status_code == 204
    with Session(app.state.engine) as s:
        assert getattr(load_preferences(s, ids["predmet"]), box) is default


@pytest.mark.parametrize(
    "bad",
    [{"template": "cards"}, {"theme": "neon"}, {"font_scale": "9"}, {"font_scale": "abc"},
     {"default_target_passes": "11"}, {"default_target_passes": "0"}, {"min_weight": "-3"}],
)
def test_a_value_that_is_not_a_choice_is_refused_and_nothing_changes(app, client, tmp_path, bad):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        save_preferences(s, ids["predmet"], Preferences(theme=Theme.NACRT))
    assert client.post(f"/predmet/{ids['predmet']}/postavke", data=full_form(**bad), headers=HX).status_code == 400
    with Session(app.state.engine) as s:
        assert load_preferences(s, ids["predmet"]) == Preferences(theme=Theme.NACRT)


def test_preferences_of_an_unknown_predmet_are_not_saved(client):
    assert client.post("/predmet/77/postavke", data=full_form(), headers=HX).status_code == 404


def test_unreadable_saved_preferences_fall_back_to_the_defaults(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        p = s.get(Predmet, ids["predmet"])
        p.preferences_json = "{ this is not json"
        s.add(p)
        s.commit()
    assert client.get(f"/predmet/{ids['predmet']}").status_code == 200
    with Session(app.state.engine) as s:
        assert load_preferences(s, ids["predmet"]) == Preferences()
        p = s.get(Predmet, ids["predmet"])
        p.preferences_json = json.dumps({"theme": "neon"})
        s.add(p)
        s.commit()
        assert load_preferences(s, ids["predmet"]) == Preferences()


def test_preferences_of_one_predmet_do_not_leak_into_another(app, client, tmp_path):
    first = add_subject(app, tmp_path)["predmet"]
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    client.post(f"/predmet/{first}/postavke", data=full_form(theme="nacrt"), headers=HX)
    with Session(app.state.engine) as s:
        assert load_preferences(s, other_id).theme is Theme.PAPIR


# --- "shows in the next build" ---------------------------------------------------------------------------------------

BUILD_CASES = [
    ("script_sample.json", {"template": "overview"}),
    ("script_sample.json", {"theme": "nacrt"}),
    ("script_sample.json", {"strictness": "verbatim_only"}),
    ("script_sample.json", {"strictness": "summary"}),
    ("script_exams_sample.json", {"focus": "most_asked", "min_weight": "4"}),
    ("script_sample.json", {"focus": "favorites"}),
    ("script_math_sample.json", {"include_figures": None}),
    ("script_sample.json", {"include_source_line": None}),
    ("script_sample.json", {"dense": "on"}),
    ("script_sample.json", {"font_scale": "1.3"}),
    ("script_sample.json", {"show_progress": None}),
    ("script_sample.json", {"default_target_passes": "5"}),
    ("script_math_sample.json", {"inline_katex": None}),
    ("script_sample.json", {"side_menu": None}),
    ("script_sample.json", {"show_mode_cards": None}),
    ("script_sample.json", {"show_tools": None}),
    ("script_sample.json", {"show_legend": None}),
]


@pytest.mark.parametrize(("fixture", "change"), BUILD_CASES, ids=lambda v: v if isinstance(v, str) else ",".join(v))
def test_a_saved_choice_changes_the_next_script(app, client, tmp_path, fixture, change):
    """Save through the page, read back what the build will read, and render: the file is different from the default."""
    ids = add_subject(app, tmp_path)
    script = Script.model_validate_json((FIXTURES / fixture).read_text(encoding="utf-8"))
    script = script.model_copy(update={"preferences": Preferences()})
    form = full_form()
    for key, value in change.items():
        if value is None:
            form.pop(key, None)
        else:
            form[key] = value
    assert client.post(f"/predmet/{ids['predmet']}/postavke", data=form, headers=HX).status_code == 204
    with Session(app.state.engine) as s:
        saved = load_preferences(s, ids["predmet"])
    assert saved != Preferences()
    options = RenderOptions(katex=None)
    before = render_script(script, options)
    after = render_script(script.model_copy(update={"preferences": saved}), options)
    assert before != after


# --- the simpler creator (D65): the look in one line, settings on their own page ------------------------------------


def test_the_subject_page_says_how_the_script_will_look_in_one_line_and_holds_no_settings(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    text = client.get(f"/predmet/{ids['predmet']}").text
    flat = " ".join(text.split())
    assert "Skripta će izgledati: <b>Papir</b> · uobičajena slova · pitanja i odgovori · igre uključene" in flat
    assert f'href="/predmet/{ids["predmet"]}/izgled">Promijeni</a>' in text and "prilagođeno" not in text
    assert 'id="prefs-form"' not in text and 'name="theme"' not in text and "Kako želim učiti" not in text
    assert '<h2 id="h-cjeline">Gradivo</h2>' in text  # the materials are the main list, not a folded panel


def test_the_one_line_says_when_the_look_was_changed(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        save_preferences(s, ids["predmet"], Preferences(theme=Theme.NACRT, show_games=False, font_scale=1.15))
    flat = " ".join(client.get(f"/predmet/{ids['predmet']}").text.split())
    assert "<b>Nacrt</b> · veća slova · pitanja i odgovori · bez igara" in flat and "prilagođeno" in flat


def test_the_settings_page_shows_the_two_main_choices_and_folds_the_rest(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    text = client.get(f"/predmet/{ids['predmet']}/izgled").text
    assert "<h1>Izgled skripte</h1>" in text and 'id="prefs-form"' in text
    assert text.index('name="theme"') < text.index("<details class=\"more\"") < text.index('name="strictness"')
    assert '<details class="more">' in text  # the standard look: everything else stays folded
    assert "Vrati standardni izgled" not in text  # nothing to undo while the look is the standard one


def test_a_changed_look_opens_all_settings_and_offers_the_way_back(app, client, tmp_path):
    ids = add_subject(app, tmp_path)
    with Session(app.state.engine) as s:
        save_preferences(s, ids["predmet"], Preferences(dense=True))
    text = client.get(f"/predmet/{ids['predmet']}/izgled").text
    assert '<details class="more" open>' in text and "Vrati standardni izgled" in text
    back = client.post(f"/predmet/{ids['predmet']}/izgled/zadano", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].endswith("/izgled")
    with Session(app.state.engine) as s:
        assert load_preferences(s, ids["predmet"]) == Preferences()
    assert "Vrati standardni izgled" not in client.get(f"/predmet/{ids['predmet']}/izgled").text


def test_the_settings_page_of_a_missing_subject_is_a_404(client):
    assert client.get("/predmet/77/izgled").status_code == 404
    assert client.post("/predmet/77/izgled/zadano").status_code == 404
