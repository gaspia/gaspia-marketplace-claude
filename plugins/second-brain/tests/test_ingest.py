import json
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

import pytest
from okfmig import ingest
from okfmig.brain import BrainSettings, init_brain
from okfmig.frontmatter import parse
from okfmig.ingest import BatchError, complete_batch, pages_updated, similar_pages
from okfmig.snapshot import load_batches, load_ledger, take_snapshot
from okfmig.tasks import load_store

Files = dict[str, str | bytes]
TODAY = date(2026, 11, 17)
OBSIDIAN = {".obsidian/app.json": "{}"}
DEFAULTS = BrainSettings()
ONBOARDING = "---\ndate: 2026-10-21\n---\n# Onboarding\n\nWe use embeddings for RAG.\n"
SUMMARY = "sources/2026-10-21-onboarding.md"
SUMMARY_TEXT = """---
type: source
title: Onboarding
---
# Onboarding

## Pages updated

* [Embeddings](../concepts/embeddings.md)
* [RAG](../concepts/rag.md)
"""
EMBEDDINGS = "---\ntype: concept\ntitle: Embeddings\naliases: [vector embeddings]\n---\n# Embeddings\n\nVectors.\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` except git's own (`git status` refreshes its index)."""
    files = (p for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in files}


def _brain(
    tmp_path: Path, make_vault: Callable[[Files], Path], files: Files, settings: BrainSettings = DEFAULTS
) -> Path:
    vault = make_vault({**OBSIDIAN, **files})
    brain = tmp_path / "brain"
    init_brain(brain)
    take_snapshot(brain, vault, None, settings, TODAY)
    return brain


def test_similar_pages(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    init_brain(brain)
    _write(
        brain,
        {
            "concepts/embeddings.md": EMBEDDINGS,
            "concepts/index.md": "# concepts\n",
            "people/orlando-ramos.md": "---\ntype: person\ntitle: Orlando Ramos\n---\n# Orlando Ramos\n",
            "notes/embedding.md": "# Embedding outside the wiki\n",
        },
    )

    hits = similar_pages(brain, "Embedding")
    assert hits[0][:2] == ("concepts/embeddings.md", "Embeddings")
    assert [h[0] for h in hits] == ["concepts/embeddings.md"]
    assert hits[0][2] >= 0.75

    assert [h[0] for h in similar_pages(brain, "Vector Embeddings")] == ["concepts/embeddings.md"]
    assert similar_pages(brain, "Vector Embeddings")[0][2] == 1.0
    assert similar_pages(brain, "Orlando") == [("people/orlando-ramos.md", "Orlando Ramos", 1.0)]
    assert similar_pages(brain, "Kubernetes") == []


def test_similar_pages_sorted_by_score_then_path(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    init_brain(brain)
    _write(
        brain,
        {
            "concepts/vector-store.md": "# x\n",
            "areas/vector-stores.md": "# x\n",
            "projects/vector-store.md": "# x\n",
        },
    )
    hits = similar_pages(brain, "vector-store")
    assert [h[0] for h in hits] == ["areas/vector-stores.md", "concepts/vector-store.md", "projects/vector-store.md"]
    assert [h[1] for h in hits] == ["vector-stores", "vector-store", "vector-store"]


def test_similar_pages_short_substring_ignored(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    init_brain(brain)
    _write(brain, {"concepts/ai-agents.md": "# x\n"})
    assert similar_pages(brain, "ai") == []


def test_pages_updated_count() -> None:
    text = """---
type: source
---
# Onboarding

## Summary

[Not counted](../concepts/summary.md)

## Pages Updated

* [Embeddings](../concepts/embeddings.md)
* [RAG](../concepts/rag.md)
* [Embeddings again](../concepts/embeddings.md#detail)
* [Orlando](../people/orlando-ramos.md)
* [Web](https://example.com/page)

### Sub-list

* [Decision](../decisions/use-rag.md)

## Other

* [Elsewhere](../concepts/other.md)
"""
    assert pages_updated(text, PurePosixPath(SUMMARY)) == 4
    assert pages_updated("# Title\n\nNo section.\n", PurePosixPath(SUMMARY)) == 0


def test_pages_updated_three_links() -> None:
    text = (
        "# S\n\n## Pages updated\n\n* [A](../concepts/a.md)\n* [B](../concepts/b.md)\n"
        "* [A](../concepts/a.md)\n* [C](../people/c.md)\n\n## Other\n\n* [D](../concepts/d.md)\n"
    )
    assert pages_updated(text, PurePosixPath(SUMMARY)) == 3


def test_complete_batch_happy_path(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(
        brain,
        {
            SUMMARY: SUMMARY_TEXT,
            "concepts/embeddings.md": EMBEDDINGS,
            "concepts/rag.md": "---\ntype: concept\ntitle: RAG\n---\n# RAG\n\nSee [Embeddings](embeddings.md).\n",
        },
    )

    lines = complete_batch(brain, "b001", TODAY)

    line = "* **Ingest**: [Onboarding](sources/2026-10-21-onboarding.md) - 2 pages updated"
    assert lines == [line]
    entry = load_ledger(brain)["vault:Onboarding.md"]
    assert entry.ingested_sha256 == entry.origin_sha256
    assert load_batches(brain) == []
    log = (brain / "log.md").read_text(encoding="utf-8")
    assert f"## 2026-11-17\n\n{line}\n" in log
    assert (brain / "concepts/index.md").is_file()
    assert (brain / "sources/index.md").is_file()
    assert "okf_version" in (parse((brain / "index.md").read_text(encoding="utf-8")).data or {})


def test_complete_batch_title_falls_back_to_stem(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: "---\ntype: source\ntitle: ''\n---\nNo heading.\n\n## Pages updated\n\nNone.\n"})

    lines = complete_batch(brain, "b001", TODAY)

    assert lines == ["* **Ingest**: [2026-10-21-onboarding](sources/2026-10-21-onboarding.md) - 0 pages updated"]


def test_complete_batch_normalizes_pages(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(
        brain,
        {
            SUMMARY: SUMMARY_TEXT,
            "concepts/embeddings.md": EMBEDDINGS,
            "concepts/rag.md": "# RAG\n\nRetrieval uses [[Embeddings]].\n",
        },
    )

    complete_batch(brain, "b001", TODAY)

    text = (brain / "concepts/rag.md").read_text(encoding="utf-8")
    data = parse(text).data or {}
    assert data["type"] == "concept"
    assert data.get("timestamp")
    assert "[Embeddings](embeddings.md)" in text
    assert "[[" not in text


def test_complete_batch_missing_summary_refused(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {"concepts/rag.md": "# RAG\n\nUses [[Embeddings]].\n"})
    before = _tree(brain)

    with pytest.raises(BatchError, match=SUMMARY):
        complete_batch(brain, "b001", TODAY)

    assert _tree(brain) == before


def test_complete_batch_blocking_plan_refused(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT, "stray.md": "# Not in a wiki folder\n"})
    before = _tree(brain)

    with pytest.raises(BatchError, match=r"stray\.md"):
        complete_batch(brain, "b001", TODAY)

    assert _tree(brain) == before


def test_complete_batch_unknown_id(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    before = _tree(brain)

    with pytest.raises(BatchError, match="b999"):
        complete_batch(brain, "b999", TODAY)
    assert _tree(brain) == before

    complete_batch(brain, "b001", TODAY)
    with pytest.raises(BatchError, match="b001"):
        complete_batch(brain, "b001", TODAY)


def test_two_batches_same_day_share_one_heading(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    second = "---\ndate: 2026-10-22\n---\n# Retro\n"
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING, "Retro.md": second}, BrainSettings(batch_size=1))
    assert [b.id for b in load_batches(brain)] == ["b001", "b002"]
    _write(brain, {SUMMARY: SUMMARY_TEXT, "sources/2026-10-22-retro.md": "# Retro\n\n## Pages updated\n"})

    first = complete_batch(brain, "b001", TODAY)
    again = complete_batch(brain, "b002", TODAY)

    log = (brain / "log.md").read_text(encoding="utf-8")
    assert log.count("## 2026-11-17") == 1
    assert log.count(first[0]) == 1
    assert log.count(again[0]) == 1
    assert again == ["* **Ingest**: [Retro](sources/2026-10-22-retro.md) - 0 pages updated"]
    assert f"## 2026-11-17\n\n{first[0]}\n{again[0]}\n" in log


def test_complete_batch_deletes_previous_copies(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    complete_batch(brain, "b001", TODAY)
    (tmp_path / "vault/Onboarding.md").write_text(ONBOARDING + "\nMore.\n", encoding="utf-8")
    take_snapshot(brain, tmp_path / "vault", None, BrainSettings(), TODAY)
    [batch] = load_batches(brain)
    previous = batch.items[0].previous
    assert previous is not None and (brain / previous).is_file()

    complete_batch(brain, batch.id, TODAY)

    assert not (brain / previous).exists()
    entry = load_ledger(brain)["vault:Onboarding.md"]
    assert entry.ingested_sha256 == entry.origin_sha256


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _git_brain(tmp_path: Path, make_vault: Callable[[Files], Path]) -> Path:
    """A brain in its own committed git repo, with the Onboarding source snapshotted (batch b001 pending)."""
    brain = tmp_path / "brain"
    init_brain(brain)
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        _git(brain, *args)
    _git(brain, "config", "commit.gpgsign", "false")
    _git(brain, "add", "-A")
    _git(brain, "commit", "-q", "-m", "init")
    vault = make_vault({**OBSIDIAN, "Onboarding.md": ONBOARDING})
    take_snapshot(brain, vault, None, DEFAULTS, TODAY)
    return brain


def test_changed_source_needs_its_summary_updated(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _git_brain(tmp_path, make_vault)
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    complete_batch(brain, "b001", TODAY)  # a new source: its summary page is untracked
    _git(brain, "add", "-A")
    _git(brain, "commit", "-q", "-m", "b001")
    (tmp_path / "vault/Onboarding.md").write_text(ONBOARDING + "\nMore.\n", encoding="utf-8")
    take_snapshot(brain, tmp_path / "vault", None, DEFAULTS, TODAY)
    [batch] = load_batches(brain)
    assert batch.items[0].status == "changed"
    before = _tree(brain)

    with pytest.raises(BatchError, match=rf"not updated.*{SUMMARY}"):
        complete_batch(brain, batch.id, TODAY)
    assert _tree(brain) == before

    summary = brain / SUMMARY
    summary.write_text(summary.read_text(encoding="utf-8") + "\n## Changes\n\nAdded More.\n", encoding="utf-8")
    complete_batch(brain, batch.id, TODAY)
    entry = load_ledger(brain)["vault:Onboarding.md"]
    assert entry.ingested_sha256 == entry.origin_sha256


def _fail_once(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make `okfmig.ingest.<name>` raise on its first call, as a crash would."""
    real = getattr(ingest, name)
    calls = []

    def flaky(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("simulated crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest, name, flaky)


@pytest.mark.parametrize("crash_in", ["add_log_entry", "save_batches"])
def test_done_rerun_after_crash_logs_each_source_once(
    tmp_path: Path, make_vault: Callable[[Files], Path], monkeypatch: pytest.MonkeyPatch, crash_in: str
) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    _fail_once(monkeypatch, crash_in)

    with pytest.raises(OSError, match="simulated crash"):
        complete_batch(brain, "b001", TODAY)
    [line] = complete_batch(brain, "b001", TODAY)

    log = (brain / "log.md").read_text(encoding="utf-8")
    assert log.count(line) == 1
    assert load_batches(brain) == []


def _write_batch_tasks(brain: Path, entries: list[dict]) -> Path:
    path = brain / ".okf-ingest" / "batches" / "b001" / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _two_entries(quote: str = "We use embeddings for RAG.") -> list[dict]:
    origin = "vault:Onboarding.md"
    return [
        {"origin": origin, "text": "Review the embeddings notes", "quote": quote},
        {"origin": origin, "text": "Share the RAG summary", "quote": quote},
    ]


def test_done_merges_tasks_and_counts_actions(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    _write_batch_tasks(brain, _two_entries())

    [line] = complete_batch(brain, "b001", TODAY)

    assert len(load_store(brain).tasks) == 2
    assert line.endswith("- 2 pages updated, 2 actions")
    assert not (brain / ".okf-ingest" / "batches" / "b001").exists()


def test_done_singular_action(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    _write_batch_tasks(brain, _two_entries()[:1])

    [line] = complete_batch(brain, "b001", TODAY)

    assert line.endswith("- 2 pages updated, 1 action")


def test_done_without_tasks_file_unchanged(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})

    [line] = complete_batch(brain, "b001", TODAY)

    assert line.endswith("pages updated")
    assert not (brain / ".okf" / "tasks.json").exists()


def test_done_refuses_bad_tasks_before_writing(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    _write_batch_tasks(brain, _two_entries("not in the snapshot"))
    before = _tree(brain)

    with pytest.raises(BatchError, match="quote"):
        complete_batch(brain, "b001", TODAY)
    assert _tree(brain) == before


def test_done_rerun_after_crash_merges_once(
    tmp_path: Path, make_vault: Callable[[Files], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = _brain(tmp_path, make_vault, {"Onboarding.md": ONBOARDING})
    _write(brain, {SUMMARY: SUMMARY_TEXT})
    _write_batch_tasks(brain, _two_entries())
    _fail_once(monkeypatch, "save_ledger")

    with pytest.raises(OSError, match="simulated crash"):
        complete_batch(brain, "b001", TODAY)
    [line] = complete_batch(brain, "b001", TODAY)

    assert len(load_store(brain).tasks) == 2
    assert (brain / "log.md").read_text(encoding="utf-8").count(line) == 1
