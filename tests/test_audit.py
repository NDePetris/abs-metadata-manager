import copy
import json

import pytest
from conftest import PRIVATE, make_book

from abs_manager.audit import analyze, folder_evidence
from abs_manager.models import Book, payload, sequence
from abs_manager.workflow import import_previous


def proposals(raw):
    return {f.field: f.proposed for f in analyze(Book(raw))[1] if f.actionable}


@pytest.mark.parametrize("value,expected", [("02.5", "2.5"), ("12.5", "12.5"), ("17.5", "17.5"),
                                           ("017", "17"), (None, None), ("NaN", None), ("-1", None)])
def test_decimal(value, expected):
    assert sequence(value) == expected


@pytest.mark.parametrize("leaf,title,seq", [("17 -Battle Ground", "Battle Ground", "17"),
                                          ("02.5 Working for Bigfoot", "Working for Bigfoot", "2.5"),
                                          ("12.5 - Side Story", "Side Story", "12.5"),
                                          ("17.5 - Side Story", "Side Story", "17.5")])
def test_folder_parsing(leaf, title, seq):
    assert folder_evidence(f"Jim Butcher/The Dresden Files/{leaf} {{James Marsters}}")["title"] == title
    assert folder_evidence(f"Author/Series/{leaf}")["sequence"] == seq


@pytest.mark.parametrize("title,series,expected", [
    ("Heaven's River: Bobiverse, Book 4", "Bobiverse", "Heaven's River"),
    ("The Gate of the Feral Gods: Dungeon Crawler Carl, Book 4", "Dungeon Crawler Carl", "The Gate of the Feral Gods"),
    ("Children of Time (Unabridged)", "Children of Time", "Children of Time"),
    ("The Wheel of Time Book 6 - Lord of Chaos", "The Wheel of Time", "Lord of Chaos"),
    ("Bobiverse, Book 4: Heaven's River", "Bobiverse", "Heaven's River"),
    ("A Title: Its Real Subtitle: Bobiverse, Book 4", "Bobiverse", "A Title: Its Real Subtitle"),
])
def test_title_rules(title, series, expected):
    assert proposals(make_book(title=title, series=series))["title"] == expected


@pytest.mark.parametrize("title", ["A History: The Meaning of Everything", "1984", "11/22/63", "The Law"])
def test_legitimate_titles_preserved(title):
    assert "title" not in proposals(make_book(title=title, series="Other Series"))


@pytest.mark.parametrize("title", ["  A Legitimate Title  ", "A  Legitimate Title"])
def test_harmless_whitespace_is_informational(title):
    raw = make_book(title=title, series=None)
    assert "title" not in proposals(raw)
    assert any(f.kind == "formatting" and not f.actionable for f in analyze(Book(raw))[1])


def test_dresden_and_canonical_author():
    raw = make_book(title="17 -Battle Ground", series="Dresden Files", seq="17", authors=["Sarah J Maas", "Other Author"])
    p = proposals(raw)
    assert p["title"] == "Battle Ground"
    assert p["series"] == [{"name": "The Dresden Files", "sequence": "17"}]
    assert p["authors"] == ["Sarah J. Maas", "Other Author"]


@pytest.mark.parametrize("leaf,clean,seq", [("17 -Battle Ground", "Battle Ground", "17"),
                                        ("02.5 Working for Bigfoot", "Working for Bigfoot", "2.5")])
def test_reimport_missing_position_requires_two_sources(leaf, clean, seq):
    raw = make_book(title=leaf, series="The Dresden Files", seq=None,
                    path=f"Jim Butcher/The Dresden Files/{leaf} {{James Marsters}}")
    raw["media"]["audioFiles"][0]["metaTags"] = {"tagTitle": clean}
    p = proposals(raw)
    assert p["title"] == clean
    assert p["series"][0]["sequence"] == seq
    raw["media"]["audioFiles"] = []
    assert "series" not in proposals(raw)


def test_folder_never_overwrites_existing_series_or_narrator():
    raw = make_book(title="The Law", series="The Dresden Files", seq="17.5", narrators=["Jim Butcher"],
                    path="Jim Butcher/The Dresden Files/18 - The Law {James Marsters}")
    assert not {"narrators", "series"} & proposals(raw).keys()


def test_hp_role_inversion_and_edition_preservation():
    raw = make_book(title="Harry Potter and the Goblet of Fire", series="Harry Potter", seq="4",
                    authors=["Stephen Fry"], narrators=["J. K. Rowling"])
    assert proposals(raw)["authors"] == ["J.K. Rowling"]
    assert proposals(raw)["narrators"] == ["Stephen Fry"]
    raw["media"]["metadata"].update(authors=[{"name": "J.K. Rowling"}], narrators=["Jim Dale", "Other Narrator"])
    assert not {"authors", "narrators"} & proposals(raw).keys()


def test_multiple_authors_narrators_translator_untouched():
    raw = make_book(authors=["Author A", "Author B"], narrators=["Narrator A", "Narrator B"])
    raw["media"]["metadata"]["translator"] = "Translator C"
    original = copy.deepcopy(raw)
    analyze(Book(raw))
    assert raw == original
    assert not {"authors", "narrators"} & proposals(raw).keys()


def test_generic_chapters_informational_and_no_duplicate_detection():
    a = make_book()
    b = make_book(item_id="second-edition", narrators=["Another Narrator"])
    for raw in (a, b):
        findings = analyze(Book(raw))[1]
        assert all(not f.actionable for f in findings if f.field in ("cover", "chapters"))
        assert any(f.kind == "informational" for f in findings)


def test_series_payload_only_names_and_string_positions():
    assert payload({"series": [{"name": "The Dresden Files", "sequence": "2.5"}]}) == {
        "metadata": {"series": [{"name": "The Dresden Files", "sequence": "2.5"}]}}
    assert payload({"authors": ["Author A", "Author B"]})["metadata"]["authors"] == [
        {"name": "Author A"}, {"name": "Author B"}]
    with pytest.raises(ValueError):
        payload({"series": [{"name": "Series", "sequence": 2.5}]})
    with pytest.raises(ValueError):
        payload({"chapters": []})


@pytest.mark.skipif(not PRIVATE.exists(), reason="Private 110-book export stays outside source control")
def test_private_export_regressions(tmp_path):
    from abs_manager.store import Store
    store = Store(tmp_path / "private.sqlite3")
    aid = import_previous(store, json.loads(PRIVATE.read_text(encoding="utf-8")))
    books = {b.values["title"]: b for b in store.books(aid)}
    assert len(books) >= 100
    assert len(store.books(aid)) == 110
    for title, clean in [("17 -Battle Ground", "Battle Ground"), ("02.5 Working for Bigfoot", "Working for Bigfoot")]:
        p = proposals(books[title].raw)
        assert p["title"] == clean
        assert p["series"][0]["sequence"] in ("17", "2.5")
    assert "narrators" not in proposals(books["The Law"].raw)
    assert all(f["status"] == "pending" for f in store.findings(aid))
