import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from okfmig.braincli import main
from okfmig.tasks import Task, TaskSource, TaskStore, load_store, save_store

Files = dict[str, str | bytes]
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "okf_brain.py"
TODAY = "2026-11-17"
OBSIDIAN = {".obsidian/app.json": "{}"}
ONBOARDING = "---\ndate: 2026-10-21\n---\n# Onboarding\n\nWe use embeddings for RAG.\n"
RAW = "raw/vault/Onboarding.md"
SUMMARY = "sources/2026-10-21-onboarding.md"
SUMMARY_TEXT = f"""---
type: source
title: Onboarding
---
# Onboarding

## Origin

[Snapshot](../{RAW})

## Pages updated

* [Embeddings](../concepts/embeddings.md)

# Citations

1. [Onboarding](../{RAW})
"""
EMBEDDINGS = f"""---
type: concept
title: Embeddings
sources:
  - resource: ../{RAW}
    title: Onboarding
---
# Embeddings

Vectors [1].

# Citations

1. [Onboarding](../{RAW})
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# repo\n", encoding="utf-8")
    _commit(path)
    return path


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit", "--allow-empty")


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def setup(tmp_path: Path, make_vault: Callable[[Files], Path]) -> tuple[Path, Path, Path]:
    """A source vault and a committed, initialized brain in the `brain/` subfolder of a repo."""
    vault = make_vault({**OBSIDIAN, "Onboarding.md": ONBOARDING})
    repo = _repo(tmp_path / "repo")
    brain = repo / "brain"
    assert main(["init", str(brain)]) == 0
    _commit(repo)
    return repo, brain, vault


def test_full_flow(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, vault = setup
    capsys.readouterr()

    assert main(["snapshot", str(brain), str(vault), "--today", TODAY]) == 0
    out = capsys.readouterr().out
    assert "new: 1" in out
    assert "b001" in out

    assert main(["next", str(brain)]) == 0
    out = capsys.readouterr().out
    for expected in ("b001", "vault:Onboarding.md", RAW, "new", SUMMARY):
        assert expected in out

    _write(brain, {SUMMARY: SUMMARY_TEXT, "concepts/embeddings.md": EMBEDDINGS})
    assert main(["done", str(brain), "b001", "--today", TODAY]) == 0
    out = capsys.readouterr().out
    assert "* **Ingest**: [Onboarding](sources/2026-10-21-onboarding.md) - 1 pages updated" in out

    assert main(["validate", str(brain)]) == 0
    assert "0 errors" in capsys.readouterr().out

    assert main(["next", str(brain)]) == 0
    assert capsys.readouterr().out.strip() == "no pending batches"


def test_init_prints_changed_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    brain = tmp_path / "brain"
    assert main(["init", str(brain)]) == 0
    out = capsys.readouterr().out
    assert ".okf/brain.yaml" in out
    assert "CLAUDE.md" in out


def test_init_warns_inside_outer_vault(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "main/.obsidian").mkdir(parents=True)
    brain = tmp_path / "main/second-brain"
    assert main(["init", str(brain)]) == 0
    err = capsys.readouterr().err
    assert "warning:" in err
    assert '"second-brain/**"' in err
    assert str((tmp_path / "main").resolve()) in err


def test_snapshot_with_pending_batches_exits_1(
    setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, brain, vault = setup
    assert main(["snapshot", str(brain), str(vault), "--today", TODAY]) == 0
    capsys.readouterr()
    assert main(["snapshot", str(brain), str(vault), "--today", TODAY]) == 1
    assert "pending batches exist; run next/done first" in capsys.readouterr().err


def test_snapshot_dirty_tree_exits_2(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, vault = setup
    _write(brain, {"concepts/draft.md": "# Draft\n"})
    capsys.readouterr()
    assert main(["snapshot", str(brain), str(vault), "--today", TODAY]) == 2
    err = capsys.readouterr().err
    assert "uncommitted changes" in err
    assert "  concepts/draft.md" in err


def test_snapshot_ignores_dirt_outside_brain_and_in_okf(
    setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, brain, vault = setup
    _write(repo, {"elsewhere.md": "# Not the brain\n"})
    _write(brain, {".okf/brain.yaml": "review:\n  mode: source\n"})
    assert main(["snapshot", str(brain), str(vault), "--today", TODAY]) == 0


def test_snapshot_outside_git_exits_2(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "Onboarding.md": ONBOARDING})
    brain = tmp_path / "brain"
    assert main(["init", str(brain)]) == 0
    assert main(["snapshot", str(brain), str(vault)]) == 2


def test_snapshot_bad_source_exits_2(setup: tuple[Path, Path, Path], tmp_path: Path) -> None:
    _, brain, _ = setup
    assert main(["snapshot", str(brain), str(tmp_path / "nope")]) == 2


def test_invalid_settings_exit_2(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    repo, brain, vault = setup
    _write(brain, {".okf/brain.yaml": "review:\n  mode: sometimes\n"})
    _commit(repo)
    assert main(["snapshot", str(brain), str(vault)]) == 2
    assert "review.mode" in capsys.readouterr().err


def test_missing_brain_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for command in (["next"], ["validate"], ["similar", "x"], ["done", "b001"]):
        name, *rest = command
        assert main([name, str(tmp_path / "nope"), *rest]) == 2
    assert "nope" in capsys.readouterr().err


def test_similar_output(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    _write(brain, {"concepts/embeddings.md": EMBEDDINGS})
    capsys.readouterr()
    assert main(["similar", str(brain), "Embedding"]) == 0
    line = capsys.readouterr().out.strip()
    score, path, title = line.split("  ")
    assert 0.75 <= float(score) <= 1.0
    assert (path, title) == ("concepts/embeddings.md", "Embeddings")


def test_done_unknown_id_exits_1(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    assert main(["done", str(brain), "b999"]) == 1
    assert "b999" in capsys.readouterr().err


def test_validate_failure_exits_1(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    _write(brain, {"people/embeddings.md": EMBEDDINGS})
    assert main(["validate", str(brain)]) == 1
    assert "error: people/embeddings.md: type 'concept'" in capsys.readouterr().out


def test_help_lists_subcommands() -> None:
    result = subprocess.run(["uv", "run", str(SCRIPT), "--help"], check=False, capture_output=True, text=True, cwd="/")
    assert result.returncode == 0
    for name in ("init", "snapshot", "next", "similar", "done", "validate"):
        assert name in result.stdout


def _case_insensitive(folder: Path) -> bool:
    probe = folder / "case-probe-a"
    probe.mkdir()
    try:
        return (folder / "CASE-PROBE-A").exists()
    finally:
        probe.rmdir()


@pytest.fixture
def brain_in_vault(tmp_path: Path) -> tuple[Path, Path]:
    """A vault repo holding an initialized, committed brain in `second-brain/`."""
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system")
    vault = _repo(tmp_path / "vault")
    _write(vault, {**OBSIDIAN, "Onboarding.md": ONBOARDING})
    brain = vault / "second-brain"
    assert main(["init", str(brain)]) == 0
    _write(brain, {"concepts/embeddings.md": EMBEDDINGS})
    _commit(vault)
    return vault, brain


def test_mixed_case_brain_path_skips_brain_pages(
    brain_in_vault: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    vault, brain = brain_in_vault
    assert main(["snapshot", str(vault / "Second-Brain"), str(vault), "--today", TODAY]) == 0
    assert "new: 2," in capsys.readouterr().out  # README.md and Onboarding.md
    ledger = (brain / ".okf/sources.json").read_text(encoding="utf-8")
    assert "vault:Onboarding.md" in ledger
    assert "embeddings" not in ledger


def test_mixed_case_brain_path_dirty_brain_exits_2(
    brain_in_vault: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    vault, brain = brain_in_vault
    _write(brain, {"concepts/draft.md": "# Draft\n"})
    capsys.readouterr()
    assert main(["snapshot", str(vault / "SECOND-BRAIN"), str(vault / "onboarding.md"), "--today", TODAY]) == 2
    assert "  concepts/draft.md" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["work:notes", "a/b", ".."])
def test_snapshot_bad_name_exits_2(
    setup: tuple[Path, Path, Path], name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _, brain, vault = setup
    assert main(["snapshot", str(brain), str(vault), "--name", name]) == 2
    assert "invalid source name" in capsys.readouterr().err
    assert not (brain / ".okf/sources.json").exists()


def _task_store(brain: Path, *tasks: Task) -> None:
    save_store(brain, TaskStore(tasks={t.id: t for t in tasks}))


def _cli_task(**overrides) -> Task:
    fields = {
        "id": "aaaaaaaa",
        "text": "Write the doc",
        "owner": "me",
        "status": "open",
        "due": None,
        "sources": [TaskSource(origin="vault:Onboarding.md", raw_path=RAW, quote="write the doc")],
        "page": None,
        "inferred": [],
        "manual": [],
        "created": "2026-11-17",
        "updated": "2026-11-17",
        "closed": None,
    }
    fields.update(overrides)
    return Task(**fields)


def test_today_prints_buckets(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    _task_store(
        brain,
        _cli_task(id="aaaaaaaa", due="2026-11-17"),
        _cli_task(id="bbbbbbbb", status="waiting", owner="Ana", text="Send the report"),
    )
    capsys.readouterr()
    assert main(["today", str(brain), "--today", "2026-11-18"]) == 0
    out = capsys.readouterr().out
    assert "Overdue (1)" in out
    assert "Waiting on others (1)" in out
    assert "aaaaaaaa  2026-11-17  me  Write the doc  (" in out
    assert "bbbbbbbb  -  Ana  Send the report" in out
    assert load_store(brain).last_today == "2026-11-18"


def test_today_empty(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    capsys.readouterr()
    assert main(["today", str(brain), "--today", "2026-11-18"]) == 0
    assert "nothing due, waiting, stale or new" in capsys.readouterr().out
    assert not (brain / ".okf" / "tasks.json").exists()


def test_task_transitions_cli(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    _task_store(brain, _cli_task())
    capsys.readouterr()
    assert main(["task", str(brain), "done", "aaaaaaaa", "--today", "2026-11-18"]) == 0
    assert "aaaaaaaa done: Write the doc" in capsys.readouterr().out
    assert load_store(brain).tasks["aaaaaaaa"].status == "done"
    assert main(["task", str(brain), "done", "aaaaaaaa"]) == 1
    assert "refused" in capsys.readouterr().err
    _task_store(brain, _cli_task())
    assert main(["task", str(brain), "defer", "aaaaaaaa"]) == 2
    assert main(["task", str(brain), "delegate", "aaaaaaaa"]) == 2
    assert main(["task", str(brain), "done", "aaaaaaaa", "--to", "Ana"]) == 2
    assert main(["task", str(brain), "done", "aaaaaaaa", "--until", "2026-12-01"]) == 2
    assert main(["task", str(brain), "delegate", "aaaaaaaa", "--to", "Ana", "--until", "2026-12-01"]) == 2


def test_task_similar_cli(setup: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _, brain, _ = setup
    _task_store(brain, _cli_task())
    capsys.readouterr()
    assert main(["task-similar", str(brain), "write the doc"]) == 0
    assert "1.00  aaaaaaaa  open  me  Write the doc" in capsys.readouterr().out
    assert main(["task-similar", str(brain), "completely unrelated zebra"]) == 0
    assert "no similar tasks" in capsys.readouterr().out


SYNC = "Sync 20261117.md"
SYNC_RAW = "raw/vault/Sync%2020261117.md"
SYNC_SUMMARY = "sources/2026-11-17-sync.md"
FIRST_ACTION = "Paulo to write up the routing decision by Friday."
REWORDED_ACTION = "Paulo to write up the Private Gateway routing decision by Friday."
SECOND_ACTION = "Ana to send the deck to Paulo."


def _sync_note(first: str) -> str:
    return f"# Sync\n\n## Actions\n\n- {first}\n- {SECOND_ACTION}\n- We should look at multi-infra sometime.\n"


def _sync_summary(extra: str = "") -> str:
    return f"""---
type: source
title: Sync
---
# Sync

## Origin

[Snapshot](../{SYNC_RAW})

## Summary

Routing follow-ups [1].{extra}

# Citations

1. [Sync](../{SYNC_RAW})
"""


def _batch_tasks(brain: Path, batch_id: str, entries: list[dict]) -> None:
    _write(brain, {f".okf-ingest/batches/{batch_id}/tasks.json": json.dumps(entries, indent=2)})


def test_ingest_with_actions_then_corrected(
    tmp_path: Path, make_vault: Callable[[Files], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    vault = make_vault({**OBSIDIAN, SYNC: _sync_note(FIRST_ACTION)})
    repo = _repo(tmp_path / "repo")
    brain = repo / "brain"
    assert main(["init", str(brain)]) == 0
    _commit(repo)
    origin = f"vault:{SYNC}"

    assert main(["snapshot", str(brain), str(vault), "--today", "2026-11-18"]) == 0
    assert main(["next", str(brain)]) == 0
    assert SYNC_SUMMARY in capsys.readouterr().out
    _write(brain, {SYNC_SUMMARY: _sync_summary()})
    first_entry = {
        "origin": origin,
        "text": "Write up the routing decision",
        "quote": FIRST_ACTION,
        "owner": "me",
        "due": "2026-11-20",
        "page": None,
        "inferred": ["due"],
        "same_as": None,
    }
    ana_entry = {
        "origin": origin,
        "text": "Send the deck",
        "quote": SECOND_ACTION,
        "owner": "Ana",
        "inferred": ["owner"],
    }
    _batch_tasks(brain, "b001", [first_entry, ana_entry])
    assert main(["done", str(brain), "b001", "--today", "2026-11-18"]) == 0
    assert "* **Ingest**: [Sync](sources/2026-11-17-sync.md) - 0 pages updated, 2 actions\n" in capsys.readouterr().out
    store = load_store(brain)
    assert len(store.tasks) == 2
    first = next(t for t in store.tasks.values() if t.owner == "me")
    _commit(repo)

    _write(vault, {SYNC: _sync_note(REWORDED_ACTION)})
    assert main(["snapshot", str(brain), str(vault), "--today", "2026-11-18"]) == 0
    assert "changed: 1" in capsys.readouterr().out
    assert main(["task-similar", str(brain), "Write up the Private Gateway routing decision"]) == 0
    assert f"  {first.id}  open  me  Write up the routing decision" in capsys.readouterr().out

    _write(brain, {SYNC_SUMMARY: _sync_summary("\n\nThe routing write-up now names the Private Gateway [1].")})
    reworded = {**first_entry, "text": "Write up the Private Gateway routing decision", "quote": REWORDED_ACTION}
    _batch_tasks(brain, "b001", [{**reworded, "same_as": first.id}])
    assert main(["done", str(brain), "b001", "--today", "2026-11-18"]) == 0
    assert "- 0 pages updated, 1 action\n" in capsys.readouterr().out
    store = load_store(brain)
    assert len(store.tasks) == 2
    assert store.tasks[first.id].text == "Write up the Private Gateway routing decision"
    assert len(store.tasks[first.id].sources) == 1
    assert store.tasks[first.id].sources[0].quote == REWORDED_ACTION

    assert main(["task", str(brain), "done", first.id, "--today", "2026-11-18"]) == 0
    assert main(["validate", str(brain)]) == 0
    capsys.readouterr()
    assert main(["today", str(brain), "--today", "2026-11-18"]) == 0
    out = capsys.readouterr().out
    assert "Waiting on others (1)" in out
    assert "  -  Ana  Send the deck  (sources/2026-11-17-sync.md)" in out
    assert "Private Gateway" not in out
