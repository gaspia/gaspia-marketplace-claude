import pytest
from okfmig.frontmatter import ensure_fields, first_h1, is_empty_value, parse, strip

BOM = "﻿"


def test_adds_block_when_missing() -> None:
    fields = {"type": "note", "title": "Hi", "timestamp": "2026-11-05T10:00:00+00:00"}
    out = ensure_fields("# Hi\nbody\n", fields)
    assert out.startswith("---\ntype: note\ntitle: Hi\ntimestamp: ")
    assert out.endswith("\n---\n# Hi\nbody\n")
    assert parse(out).data == fields


def test_preserves_existing_block_verbatim() -> None:
    text = "---\nnotion-id: 2c5  # keep\nGoal: x\n---\nbody\n"
    out = ensure_fields(text, {"type": "note", "title": "T", "timestamp": "2026-11-05T10:00:00+00:00"})
    assert out.startswith("---\nnotion-id: 2c5  # keep\nGoal: x\ntype: note\ntitle: T\ntimestamp: ")
    assert out.endswith("\n---\nbody\n")


def test_fills_empty_type_in_place() -> None:
    assert ensure_fields("---\ntype:\ntags: [a]\n---\n", {"type": "note"}) == "---\ntype: note\ntags: [a]\n---\n"


@pytest.mark.parametrize("empty", ['type: ""', "type: null", "type: ~", "type:"])
def test_fills_empty_variants(empty: str) -> None:
    assert ensure_fields(f"---\n{empty}\n---\n", {"type": "note"}) == "---\ntype: note\n---\n"


def test_keeps_capital_Type_and_adds_type() -> None:
    out = ensure_fields("---\nType: Meeting\n---\n", {"type": "meeting"})
    assert "Type: Meeting" in out
    assert "type: meeting" in out


def test_noop_when_complete() -> None:
    text = "---\ntype: x\ntitle: y\n---\nbody\n"
    assert ensure_fields(text, {"type": "note", "title": "Z"}) == text


def test_invalid_yaml_reported() -> None:
    p = parse("---\na: [\n---\n")
    assert p.data is None
    assert p.error
    p2 = parse("---\n- a\n---\n")
    assert p2.data is None
    assert p2.error


def test_crlf_and_bom() -> None:
    text = f"{BOM}---\r\nGoal: x\r\n---\r\nbody\r\n"
    p = parse(text)
    assert p.has_block
    assert p.newline == "\r\n"
    assert p.body == "body\r\n"
    assert ensure_fields(text, {"type": "note"}) == f"{BOM}---\r\nGoal: x\r\ntype: note\r\n---\r\nbody\r\n"


@pytest.mark.parametrize(
    "t", ["M2. O11 Infras: Portfolios", "Chat with Architect - João", "#tag-like", "yes", "2026-11-09"]
)
def test_special_titles_round_trip(t: str) -> None:
    title = parse(ensure_fields("", {"title": t})).data["title"]  # type: ignore[index]
    assert isinstance(title, str)
    assert title == t


def test_empty_file() -> None:
    assert ensure_fields("", {"type": "note"}) == "---\ntype: note\n---\n"


def test_empty_block_and_no_block() -> None:
    p = parse("---\n---\nbody\n")
    assert p.has_block
    assert p.data == {}
    assert not parse("# Hi\n").has_block
    assert not parse("---\na: 1\n").has_block


def test_timestamp_values_do_not_crash() -> None:
    text = "---\ncreated: 2026-11-05T10:00:00+00:00\ntype: x\n---\n"
    assert parse(text).data is not None
    assert ensure_fields(text, {"type": "note", "title": "T"}).count("title: T") == 1


def test_strip() -> None:
    assert strip("---\ntype: index\n---\n\n# C\n") == "# C\n"


def test_strip_bom_crlf_and_no_block() -> None:
    assert strip(f"{BOM}---\r\na: 1\r\n---\r\n\r\n# C\r\n") == f"{BOM}# C\r\n"
    assert strip("# C\n") == "# C\n"


def test_first_h1() -> None:
    assert first_h1("text\n# Title \n## Sub\n") == "Title"
    assert first_h1("## Sub\n") is None


def test_is_empty_value() -> None:
    assert is_empty_value(None)
    assert is_empty_value("  ")
    assert not is_empty_value("x")
    assert not is_empty_value(0)
    assert not is_empty_value([])
