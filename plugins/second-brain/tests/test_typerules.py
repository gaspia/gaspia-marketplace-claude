from pathlib import Path, PurePosixPath

import pytest
from okfmig.typerules import Rule, TypeConfig, is_kebab, load, resolve_type


def test_precedence() -> None:
    cfg = TypeConfig(default="x", rules=[Rule("folder", "**", "note")], overrides={"a.md": "meeting"})
    assert resolve_type(cfg, PurePosixPath("a.md"), {"type": "Concept"}, set()) == ("Concept", "existing")
    assert resolve_type(cfg, PurePosixPath("a.md"), {}, set()) == ("meeting", "override")
    assert resolve_type(cfg, PurePosixPath("b.md"), {}, set()) == ("note", "rule 1: folder **")


def test_empty_type_is_missing() -> None:
    cfg = TypeConfig(default="note")
    assert resolve_type(cfg, PurePosixPath("a.md"), {"type": ""}, set()) == ("note", "default")


def test_each_rule_kind() -> None:
    def run(rule: Rule, path: str, data: dict, tags: set[str] | None = None) -> tuple[str | None, str]:
        return resolve_type(TypeConfig(rules=[rule]), PurePosixPath(path), data, tags or set())

    folder = Rule("folder", "second-brain/concepts/**", "concept")
    assert run(folder, "second-brain/concepts/embeddings.md", {})[0] == "concept"
    tag = Rule("tag", "meeting", "meeting")
    assert run(tag, "a.md", {"tags": "Meeting, x"})[0] == "meeting"
    assert run(tag, "a.md", {"tags": ["#Meeting"]})[0] == "meeting"
    assert run(tag, "a.md", {}, {"meeting"})[0] == "meeting"
    assert run(tag, "a.md", {"tags": 5})[0] is None
    assert run(Rule("field", "Type", "meeting", "Meeting"), "a.md", {"Type": "Meeting"})[0] == "meeting"
    assert run(Rule("filename", "^o2o ", "one-on-one"), "Work/o2o Batista.md", {})[0] == "one-on-one"


def test_unclassified() -> None:
    assert resolve_type(TypeConfig(), PurePosixPath("a.md"), {}, set()) == (None, "unclassified")


def test_load_rejects_bad_type(tmp_path: Path) -> None:
    p = tmp_path / "types.yaml"
    p.write_text("rules:\n  - tag: meeting\n    type: Meeting Notes\n")
    with pytest.raises(ValueError, match="Meeting Notes"):
        load(p)


def test_load_rejects_unknown_rule_shape(tmp_path: Path) -> None:
    p = tmp_path / "types.yaml"
    p.write_text("rules:\n  - colour: red\n    type: note\n")
    with pytest.raises(ValueError):
        load(p)


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.yaml") == TypeConfig()


def test_load_full(tmp_path: Path) -> None:
    p = tmp_path / "types.yaml"
    p.write_text(
        'default: note\nexclude: ["Templates/**"]\nrules:\n  - folder: "a/**"\n    type: concept\n'
        "  - field: { name: Type, value: Meeting }\n    type: meeting\n"
        'overrides:\n  "W/x.md": meeting\nreserved:\n  "W/index.md": note\n'
    )
    cfg = load(p)
    assert cfg.default == "note" and cfg.exclude == ["Templates/**"]
    assert cfg.rules == [Rule("folder", "a/**", "concept"), Rule("field", "Type", "meeting", "Meeting")]
    assert cfg.overrides == {"W/x.md": "meeting"} and cfg.reserved == {"W/index.md": "note"}


def test_is_kebab() -> None:
    assert is_kebab("one-on-one") and not is_kebab("Meeting Notes") and not is_kebab("a--b")


def test_load_yaml_syntax_error_is_value_error_with_path(tmp_path: Path) -> None:
    path = tmp_path / "types.yaml"
    path.write_text("rules:\n  - folder: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"types\.yaml"):
        load(path)
