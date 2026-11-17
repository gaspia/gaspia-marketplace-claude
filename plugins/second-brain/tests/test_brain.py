import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from okfmig import brain
from okfmig.brain import BrainSettings, init_brain, load_settings, outer_vault
from okfmig.typerules import load

Files = dict[str, str | bytes]


def _settings(root: Path, text: str) -> Path:
    (root / ".okf").mkdir(parents=True)
    (root / ".okf" / "brain.yaml").write_text(text, encoding="utf-8")
    return root


def test_settings_defaults_and_overrides(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "none") == BrainSettings()
    root = _settings(tmp_path / "a", "review:\n  mode: source\n")
    s = load_settings(root)
    assert s.review_mode == "source" and s.batch_size == 10
    assert s.effective_batch_size(False) == 1
    root = _settings(tmp_path / "b", "review:\n  mode: autonomous\n  batch_size: 5\npeople:\n  personal: false\n")
    s = load_settings(root)
    assert s.effective_batch_size(False) == 5 and s.effective_batch_size(True) == 1
    assert s.people_personal is False and s.people_performance is True


def test_settings_invalid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"review\.mode"):
        load_settings(_settings(tmp_path / "a", "review:\n  mode: weekly\n"))
    with pytest.raises(ValueError, match=r"batch_size"):
        load_settings(_settings(tmp_path / "b", "review:\n  batch_size: 0\n"))
    with pytest.raises(ValueError, match=r"people\.personal"):
        load_settings(_settings(tmp_path / "c", "people:\n  personal: maybe\n"))


def test_init_creates_layout(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    changed = init_brain(root)
    for folder in brain.WIKI_FOLDERS:
        assert (root / folder).is_dir()
    assert (root / "raw").is_dir()
    assert "review:" in (root / ".okf" / "brain.yaml").read_text(encoding="utf-8")
    cfg = load(root / ".okf" / "types.yaml")
    assert [(r.kind, r.pattern, r.type) for r in cfg.rules] == [
        ("folder", f"{f}/**", t) for f, t in brain.WIKI_FOLDERS.items()
    ]
    assert cfg.exclude == ["raw/**"]
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert brain.MARKER_START in text and brain.MARKER_END in text
    assert "CLAUDE.md" in changed and ".okf/types.yaml" in changed
    assert init_brain(root) == []


def test_init_merges_existing(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    (root / ".okf").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# Mine\nkeep me\n", encoding="utf-8")
    custom = 'rules:\n  - folder: "notes/**"\n    type: note\n  - folder: "concepts/**"\n    type: concept\n'
    (root / ".okf" / "types.yaml").write_text(custom, encoding="utf-8")
    init_brain(root)
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith(f"# Mine\nkeep me\n\n{brain.MARKER_START}\n")
    cfg = load(root / ".okf" / "types.yaml")
    assert [r.pattern for r in cfg.rules][:2] == ["notes/**", "concepts/**"]
    assert len(cfg.rules) == 7 and cfg.exclude == ["raw/**"]
    snapshot = {p: p.read_bytes() for p in (root / "CLAUDE.md", root / ".okf" / "types.yaml")}
    assert init_brain(root) == []
    assert all(p.read_bytes() == data for p, data in snapshot.items())


def test_init_keeps_crlf_and_existing_exclude(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    (root / ".okf").mkdir(parents=True)
    (root / "CLAUDE.md").write_bytes(b"# Mine\r\nkeep\r\n")
    (root / ".okf" / "types.yaml").write_text("exclude:\n  - drafts/**\n", encoding="utf-8")
    init_brain(root)
    data = (root / "CLAUDE.md").read_bytes()
    assert data.startswith(b"# Mine\r\nkeep\r\n\r\n<!--") and b"\n" not in data.replace(b"\r\n", b"")
    assert load(root / ".okf" / "types.yaml").exclude == ["drafts/**", "raw/**"]
    assert init_brain(root) == []


def test_init_replaces_schema_block(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    old = f"before\n\n{brain.MARKER_START}\nold text\n{brain.MARKER_END}\n\nafter\n"
    (root / "CLAUDE.md").write_text(old, encoding="utf-8")
    init_brain(root)
    new = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert new.startswith(f"before\n\n{brain.MARKER_START}\n") and new.endswith(f"{brain.MARKER_END}\n\nafter\n")
    assert "old text" not in new and "Three layers" in new
    assert new.count(brain.MARKER_START) == 1


def test_outer_vault(tmp_path: Path) -> None:
    (tmp_path / "V" / ".obsidian").mkdir(parents=True)
    (tmp_path / "V" / "second-brain").mkdir()
    assert outer_vault(tmp_path / "V" / "second-brain") == (tmp_path / "V").resolve()
    (tmp_path / "solo").mkdir()
    (tmp_path / "solo" / ".obsidian").mkdir()
    assert outer_vault(tmp_path / "solo") is None


def _excludes(root: Path) -> list[str]:
    return (root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_init_git_exclude_subfolder_and_top(git_vault: Callable[[Files], Path], tmp_path: Path) -> None:
    root = git_vault({"second-brain/a.md": "x"})
    init_brain(root / "second-brain")
    assert "second-brain/.okf-ingest/" in _excludes(root)
    other = tmp_path / "own"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    init_brain(other)
    assert ".okf-ingest/" in _excludes(other)


def test_schema_template_mirrors_page_types() -> None:
    text = brain.SCHEMA_TEMPLATE.read_text(encoding="utf-8")
    for rule in (
        "sources/<YYYY-MM-DD>-<slug>.md",
        "When a page earns its place",
        "never placeholder text",
        "`# Citations`",
    ):
        assert rule in text, rule
    assert "—" not in text
