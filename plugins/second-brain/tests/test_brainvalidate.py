from collections.abc import Callable
from datetime import date
from pathlib import Path

from okfmig.brain import BrainSettings, init_brain
from okfmig.brainvalidate import validate_brain
from okfmig.ingest import complete_batch
from okfmig.snapshot import take_snapshot

Files = dict[str, str | bytes]
TODAY = date(2026, 11, 17)
OBSIDIAN = {".obsidian/app.json": "{}"}
ONBOARDING = "---\ndate: 2026-10-21\n---\n# Onboarding\n\nWe use embeddings for RAG. Orlando leads it.\n"
RAW = "raw/vault/Onboarding.md"
SUMMARY = "sources/2026-10-21-onboarding.md"
SUMMARY_TEXT = f"""---
type: source
title: Onboarding
sources:
  - resource: ../{RAW}
    title: Onboarding
---
# Onboarding

## Origin

[Snapshot](../{RAW}), from `Onboarding.md` in the vault.

## Pages updated

* [Embeddings](../concepts/embeddings.md)
* [Orlando Ramos](../people/orlando-ramos.md)

# Citations

1. [Onboarding](../{RAW})
"""
EMBEDDINGS = f"""---
type: concept
title: Embeddings
sources:
  - resource: {RAW}
    title: Onboarding
---
# Embeddings

Vectors used for retrieval [1].

# Citations

1. [Onboarding](../{RAW})
2. [Background](https://example.com/embeddings)
"""
ORLANDO = f"""---
type: person
title: Orlando Ramos
sources:
  - ../{RAW}
---
# Orlando Ramos

## Role

Leads the RAG work [1].

## Performance

Strong delivery [1].

# Citations

1. [Onboarding](../{RAW})
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _done_brain(tmp_path: Path, make_vault: Callable[[Files], Path]) -> Path:
    vault = make_vault({**OBSIDIAN, "Onboarding.md": ONBOARDING})
    brain = tmp_path / "brain"
    init_brain(brain)
    take_snapshot(brain, vault, None, BrainSettings(), TODAY)
    _write(brain, {SUMMARY: SUMMARY_TEXT, "concepts/embeddings.md": EMBEDDINGS, "people/orlando-ramos.md": ORLANDO})
    complete_batch(brain, "b001", TODAY)
    return brain


def _own_warnings(warnings: list[str]) -> list[str]:
    """Warnings besides the inherited `outside bundle: CLAUDE.md`."""
    return [w for w in warnings if w != "outside bundle: CLAUDE.md"]


def _errors_besides_broken_links(errors: list[str]) -> list[str]:
    return [e for e in errors if "broken link" not in e]


def test_completed_brain_validates_clean(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    report = validate_brain(brain)
    assert report.errors == []
    assert report.warnings == ["outside bundle: CLAUDE.md"]  # never one per raw snapshot


def test_concept_page_in_people_folder(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {"people/embeddings.md": EMBEDDINGS})
    assert validate_brain(brain).errors == [
        "people/embeddings.md: type 'concept' does not match its folder people/ (expected 'person')"
    ]


def test_type_checked_at_any_depth(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {"people/team/embeddings.md": EMBEDDINGS.replace("../raw", "../../raw")})
    assert validate_brain(brain).errors == [
        "people/team/embeddings.md: type 'concept' does not match its folder people/ (expected 'person')"
    ]


def test_sources_resource_missing(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    page = brain / "concepts/embeddings.md"
    page.write_text(page.read_text(encoding="utf-8").replace(f"resource: {RAW}", "resource: raw/x/missing.md"))
    assert validate_brain(brain).errors == [
        "concepts/embeddings.md: sources entry raw/x/missing.md is not an existing file under raw/"
    ]


def test_sources_resource_outside_raw(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    page = brain / "concepts/embeddings.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(f"resource: {RAW}", "resource: ../people/orlando-ramos.md")
    )
    assert validate_brain(brain).errors == [
        "concepts/embeddings.md: sources entry ../people/orlando-ramos.md is not an existing file under raw/"
    ]


def test_citation_outside_raw(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    page = brain / "concepts/embeddings.md"
    page.write_text(page.read_text(encoding="utf-8") + "3. [Orlando](../people/orlando-ramos.md)\n")
    assert validate_brain(brain).errors == [
        "concepts/embeddings.md: citation people/orlando-ramos.md is not an existing file under raw/"
    ]


def test_summary_page_without_raw_link(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {"sources/2026-10-22-retro.md": "---\ntype: source\ntitle: Retro\n---\n# Retro\n\nNo origin.\n"})
    assert validate_brain(brain).errors == ["sources/2026-10-22-retro.md: source summary page links no raw snapshot"]


def test_raw_snapshot_edited(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    with (brain / RAW).open("a", encoding="utf-8") as fh:
        fh.write("Edited.\n")
    assert validate_brain(brain).errors == [f"{RAW}: raw snapshot was edited"]


def test_summary_page_deleted(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    (brain / SUMMARY).unlink()
    errors = validate_brain(brain).errors
    assert _errors_besides_broken_links(errors) == [
        f"{SUMMARY}: summary page of ingested source vault:Onboarding.md is missing"
    ]


def test_performance_section_with_setting_off_warns(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {".okf/brain.yaml": "people:\n  performance: false\n"})
    report = validate_brain(brain)
    assert report.errors == []
    assert _own_warnings(report.warnings) == [
        "people/orlando-ramos.md: has a ## Performance section while people.performance is off"
    ]


def test_personal_section_case_insensitive(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {".okf/brain.yaml": "people:\n  personal: false\n"})
    page = brain / "people/orlando-ramos.md"
    page.write_text(page.read_text(encoding="utf-8").replace("## Role", "## PERSONAL\n\nTwo kids.\n\n## Role"))
    report = validate_brain(brain)
    assert report.errors == []
    assert _own_warnings(report.warnings) == [
        "people/orlando-ramos.md: has a ## Personal section while people.personal is off"
    ]


def test_deleted_origin_is_not_an_error(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    (tmp_path / "vault/Onboarding.md").unlink()
    result = take_snapshot(brain, tmp_path / "vault", None, BrainSettings(), TODAY)
    assert result.missing == ["vault:Onboarding.md"]
    report = validate_brain(brain)
    assert report.errors == []
    assert _own_warnings(report.warnings) == []


def test_missing_snapshot_file_warns(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    (brain / RAW).unlink()
    report = validate_brain(brain)
    assert f"{RAW}: raw snapshot of vault:Onboarding.md is missing" in report.warnings
    assert all("raw snapshot was edited" not in e for e in report.errors)


def test_workdir_is_never_part_of_the_bundle(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _done_brain(tmp_path, make_vault)
    _write(brain, {".okf-ingest/previous/raw/vault/Old.md": "# no frontmatter\n"})
    report = validate_brain(brain)
    assert report.errors == []
    assert _own_warnings(report.warnings) == []
