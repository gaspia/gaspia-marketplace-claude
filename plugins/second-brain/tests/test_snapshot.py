import json
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

import pytest
from okfmig.brain import BrainSettings, init_brain
from okfmig.gitutil import ignored_under
from okfmig.links import embed_targets
from okfmig.snapshot import (
    load_batches,
    load_ledger,
    save_ledger,
    source_root,
    take_snapshot,
)

Files = dict[str, str | bytes]
TODAY = date(2026, 11, 17)
BATCH = BrainSettings()
OBSIDIAN = {".obsidian/app.json": "{}"}


def _brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    init_brain(brain)
    return brain


def _snapshot_files(brain: Path) -> set[str]:
    return {p.relative_to(brain).as_posix() for p in (brain / "raw").rglob("*") if p.is_file()}


def test_first_snapshot_copies_bytes(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    crlf = b"---\r\ntitle: A\r\n---\r\n# A\r\nline\r\n"
    linked = "---\ntitle: [unclosed\n---\nSee [[A]] and [[Missing|alias]].\n"
    vault = make_vault({**OBSIDIAN, "Work/a.md": crlf, "Work/b.md": linked, "index.md": "# Home\n"})
    brain = _brain(tmp_path)

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.name == "vault"
    assert sorted(result.new) == ["vault:Work/a.md", "vault:Work/b.md", "vault:index.md"]
    assert (brain / "raw/vault/Work/a.md").read_bytes() == crlf
    assert (brain / "raw/vault/Work/b.md").read_bytes() == linked.encode()
    assert (brain / "raw/vault/index.md").read_bytes() == b"# Home\n"
    ledger = load_ledger(brain)
    assert len(ledger) == 3
    assert all(e.ingested_sha256 is None for e in ledger.values())
    entry = ledger["vault:Work/a.md"]
    assert entry.raw_path == "raw/vault/Work/a.md"
    assert entry.first_seen == entry.last_snapshot == "2026-11-17"
    batches = load_batches(brain)
    assert [b.id for b in batches] == ["b001"] and result.batches == ["b001"]
    assert [i.origin for i in batches[0].items] == ["vault:Work/a.md", "vault:Work/b.md", "vault:index.md"]
    assert {i.status for i in batches[0].items} == {"new"}
    assert all(i.previous is None for i in batches[0].items)
    raw = json.loads((brain / ".okf/sources.json").read_text(encoding="utf-8"))
    assert list(raw) == sorted(raw)
    assert _snapshot_files(brain) == {"raw/vault/Work/a.md", "raw/vault/Work/b.md", "raw/vault/index.md"}


def test_unchanged_skipped_and_changed_keeps_previous(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "one\n", "b.md": "two\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    before = (brain / ".okf-ingest/batches.json").read_bytes()

    again = take_snapshot(brain, vault, None, BATCH, date(2026, 11, 18))
    assert again.new == [] and again.changed == []
    assert sorted(again.unchanged) == ["vault:a.md", "vault:b.md"]
    assert again.batches == []
    assert (brain / ".okf-ingest/batches.json").read_bytes() == before

    # batch b001 done: ledger marked ingested, batches cleared
    ledger = load_ledger(brain)
    for entry in ledger.values():
        entry.ingested_sha256 = entry.origin_sha256
    save_ledger(brain, ledger)
    (brain / ".okf-ingest/batches.json").write_text("[]", encoding="utf-8")

    (vault / "a.md").write_text("one changed\n", encoding="utf-8")
    changed = take_snapshot(brain, vault, None, BATCH, date(2026, 11, 19))
    assert changed.changed == ["vault:a.md"]
    assert (brain / ".okf-ingest/previous/raw/vault/a.md").read_bytes() == b"one\n"
    assert (brain / "raw/vault/a.md").read_bytes() == b"one changed\n"
    entry = load_ledger(brain)["vault:a.md"]
    assert entry.first_seen == "2026-11-17" and entry.last_snapshot == "2026-11-19"
    assert entry.ingested_sha256 != entry.origin_sha256
    [batch] = load_batches(brain)
    assert batch.id == "b001"
    [item] = batch.items
    assert item.status == "changed"
    assert item.previous == ".okf-ingest/previous/raw/vault/a.md"
    assert item.summary_page == entry.summary_page

    # a second change before ingest keeps the diff against the last ingested version
    (brain / ".okf-ingest/batches.json").write_text("[]", encoding="utf-8")
    (vault / "a.md").write_text("one changed twice\n", encoding="utf-8")
    take_snapshot(brain, vault, None, BATCH, date(2026, 11, 20))
    assert (brain / ".okf-ingest/previous/raw/vault/a.md").read_bytes() == b"one\n"


def test_summary_page_assignment(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault(
        {
            **OBSIDIAN,
            "Onboarding.md": "---\ncreated: 2026-10-21T10:00\n---\nbody\n",
            "2026-11-09 Sync.md": "sync\n",
            "2026-11-09.md": "daily\n",
            "Plain Note.md": "plain\n",
            "A/Retro.md": "---\ndate: 2026-09-01\n---\n",
            "B/Retro.md": "---\ndate: 2026-09-01\n---\n",
            "Taken.md": "taken\n",
        }
    )
    brain = _brain(tmp_path)
    (brain / "sources/2026-11-17-taken.md").write_text("existing\n", encoding="utf-8")

    take_snapshot(brain, vault, None, BATCH, TODAY)

    pages = {origin: e.summary_page for origin, e in load_ledger(brain).items()}
    assert pages == {
        "vault:Onboarding.md": "sources/2026-10-21-onboarding.md",
        "vault:2026-11-09 Sync.md": "sources/2026-11-09-sync.md",
        "vault:2026-11-09.md": "sources/2026-11-09-note.md",
        "vault:Plain Note.md": "sources/2026-11-17-plain-note.md",
        "vault:A/Retro.md": "sources/2026-09-01-retro.md",
        "vault:B/Retro.md": "sources/2026-09-01-retro-2.md",
        "vault:Taken.md": "sources/2026-11-17-taken-2.md",
    }


def test_single_note_source_relative_to_vault(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "Work/a.md": "a\n", "Work/b.md": "b\n"})
    brain = _brain(tmp_path)
    settings = BrainSettings(review_mode="autonomous", batch_size=10)

    assert source_root(vault / "Work/a.md") == vault
    result = take_snapshot(brain, vault / "Work/a.md", None, settings, TODAY)

    assert result.name == "vault"
    assert result.new == ["vault:Work/a.md"]
    assert load_ledger(brain)["vault:Work/a.md"].raw_path == "raw/vault/Work/a.md"
    assert (brain / "raw/vault/Work/a.md").read_bytes() == b"a\n"
    [batch] = load_batches(brain)
    assert len(batch.items) == 1


def test_folder_source_scoped_and_named(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "Work/a.md": "a\n", "Home/b.md": "b\n"})
    brain = _brain(tmp_path)

    result = take_snapshot(brain, vault / "Work", "My Notes", BATCH, TODAY)

    assert result.name == "My Notes"
    assert result.new == ["My Notes:Work/a.md"]
    assert _snapshot_files(brain) == {"raw/My Notes/Work/a.md"}


def test_brain_inside_source_never_snapshotted(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = vault / "second-brain"
    init_brain(brain)
    (brain / "concepts/x.md").write_text("x\n", encoding="utf-8")
    other = vault / "old-brain"
    init_brain(other)
    (other / "raw/src/n.md").parent.mkdir(parents=True)
    (other / "raw/src/n.md").write_text("n\n", encoding="utf-8")
    (other / "concepts/y.md").write_text("y\n", encoding="utf-8")

    take_snapshot(brain, vault, None, BATCH, TODAY)
    origins = set(load_ledger(brain))
    assert not any(o.startswith("vault:second-brain/") for o in origins)
    assert not any(o.startswith("vault:old-brain/raw/") for o in origins)
    assert "vault:a.md" in origins and "vault:old-brain/concepts/y.md" in origins

    again = take_snapshot(brain, vault, None, BATCH, TODAY)
    assert again.new == []


def test_attachments_copied(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    png = b"\x89PNG\r\n\x1a\nfake"
    vault = make_vault(
        {
            **OBSIDIAN,
            "Work/Meet.md": (
                "![[Pasted image 1.png]]\n![[Pasted image 1.png|200]]\n![[Other]]\n![[gone.png]]\n"
                "![chart](attachments/chart%20one.svg) ![web](https://example.com/x.png)\n"
                "`![[code.png]]`\n"
            ),
            "Work/attachments/Pasted image 1.png": png,
            "Work/attachments/chart one.svg": "<svg/>",
            "Other.md": "other\n",
            "code.png": b"code",
        }
    )
    brain = _brain(tmp_path)

    first = take_snapshot(brain, vault / "Work", None, BATCH, TODAY)

    assert first.attachments == 2
    assert (brain / "raw/vault/Work/attachments/Pasted image 1.png").read_bytes() == png
    assert (brain / "raw/vault/Work/attachments/chart one.svg").read_bytes() == b"<svg/>"
    assert not (brain / "raw/vault/code.png").exists()
    assert not (brain / "raw/vault/Other.md").exists()
    assert take_snapshot(brain, vault / "Work", None, BATCH, TODAY).attachments == 0

    (vault / "Work/attachments/chart one.svg").write_text("<svg>v2</svg>", encoding="utf-8")
    assert take_snapshot(brain, vault / "Work", None, BATCH, TODAY).attachments == 1


def test_batches_by_mode(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, **{f"n{i:02}.md": f"{i}\n" for i in range(12)}})
    cases = [
        (BrainSettings(review_mode="batch", batch_size=10), [10, 2]),
        (BrainSettings(review_mode="source", batch_size=10), [1] * 12),
        (BrainSettings(review_mode="autonomous", batch_size=5), [5, 5, 2]),
    ]
    for n, (settings, sizes) in enumerate(cases):
        brain = tmp_path / f"brain{n}"
        init_brain(brain)
        result = take_snapshot(brain, vault, None, settings, TODAY)
        batches = load_batches(brain)
        assert [len(b.items) for b in batches] == sizes
        assert result.batches == [f"b{i:03}" for i in range(1, len(sizes) + 1)]
        assert [i.origin for b in batches for i in b.items] == [f"vault:n{i:02}.md" for i in range(12)]


def test_batches_continue_ids_without_duplicates(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    (vault / "a.md").write_text("a2\n", encoding="utf-8")
    (vault / "b.md").write_text("b\n", encoding="utf-8")

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.unchanged == ["vault:a.md"] and result.new == ["vault:b.md"] and result.changed == []
    assert (brain / "raw/vault/a.md").read_bytes() == b"a2\n"
    batches = load_batches(brain)
    assert [b.id for b in batches] == ["b001", "b002"]
    assert [i.origin for i in batches[1].items] == ["vault:b.md"]


def test_missing_origin_reported(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n", "b.md": "b\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    (vault / "a.md").unlink()

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.missing == ["vault:a.md"]
    assert "vault:a.md" in load_ledger(brain)
    assert (brain / "raw/vault/a.md").exists()


def test_undecodable_and_ignored_sources(tmp_path: Path, git_vault: Callable[[Files], Path]) -> None:
    latin = "caf\xe9\n".encode("latin-1")
    vault = git_vault({**OBSIDIAN, ".gitignore": "private/\n", "a.md": latin, "private/s.md": "secret\n"})
    brain = _brain(tmp_path)

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.new == ["vault:a.md"]
    assert (brain / "raw/vault/a.md").read_bytes() == latin


def test_source_root_without_obsidian(tmp_path: Path) -> None:
    folder = tmp_path / "notes"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub/a.md").write_text("a\n", encoding="utf-8")
    assert source_root(folder) == folder
    assert source_root(folder / "sub/a.md") == folder / "sub"


def test_ignored_under_strips_prefix(tmp_path: Path) -> None:
    top = tmp_path / "repo"
    sub = top / "vault"
    (sub / "private").mkdir(parents=True)
    (sub / "private/x.md").write_text("x\n", encoding="utf-8")
    (sub / "keep.md").write_text("k\n", encoding="utf-8")
    (top / "other").mkdir()
    (top / "other/y.md").write_text("y\n", encoding="utf-8")
    (top / ".gitignore").write_text("private/\nother/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=top, check=True)

    assert ignored_under(sub) == [PurePosixPath("private")]
    assert ignored_under(top) == [PurePosixPath("other"), PurePosixPath("vault/private")]
    assert ignored_under(tmp_path / "nowhere") == []


def test_embed_targets() -> None:
    text = "![[img.png|100]] [[note]] ![[doc#sec]] ![a](x%20y.png) [b](z.png) `![[c.png]]` ![w](http://h/x.png)\n"
    assert embed_targets(text) == [("wiki", "img.png"), ("wiki", "doc"), ("md", "x y.png")]


def _lose_batches(brain: Path) -> None:
    (brain / ".okf-ingest/batches.json").unlink()


def _mark_ingested(brain: Path, origin: str) -> None:
    ledger = load_ledger(brain)
    ledger[origin].ingested_sha256 = ledger[origin].origin_sha256
    save_ledger(brain, ledger)


def test_lost_batches_requeue_uningested(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _lose_batches(brain)

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.new == ["vault:a.md"] and result.unchanged == []
    [batch] = load_batches(brain)
    [item] = batch.items
    assert item.status == "new" and item.previous is None


def test_edit_before_ingest_stays_new(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _lose_batches(brain)
    (vault / "a.md").write_text("a2\n", encoding="utf-8")

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.new == ["vault:a.md"] and result.changed == []
    [item] = load_batches(brain)[0].items
    assert item.status == "new" and item.previous is None
    assert not (brain / ".okf-ingest/previous").exists()
    assert (brain / "raw/vault/a.md").read_bytes() == b"a2\n"


def test_previous_is_ingested_version_across_lost_batches(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "v1\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _mark_ingested(brain, "vault:a.md")
    _lose_batches(brain)

    (vault / "a.md").write_text("v2\n", encoding="utf-8")
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _lose_batches(brain)
    (vault / "a.md").write_text("v3\n", encoding="utf-8")
    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.changed == ["vault:a.md"]
    [item] = load_batches(brain)[0].items
    assert item.status == "changed" and item.previous == ".okf-ingest/previous/raw/vault/a.md"
    assert (brain / item.previous).read_bytes() == b"v1\n"
    assert (brain / "raw/vault/a.md").read_bytes() == b"v3\n"


def test_stale_previous_replaced_or_dropped(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "v1\n", "b.md": "b1\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _mark_ingested(brain, "vault:a.md")
    _mark_ingested(brain, "vault:b.md")
    _lose_batches(brain)
    stale = brain / ".okf-ingest/previous/raw/vault"
    stale.mkdir(parents=True)
    (stale / "a.md").write_text("stale\n", encoding="utf-8")
    (stale / "b.md").write_text("stale\n", encoding="utf-8")
    (brain / "raw/vault/b.md").write_text("tampered\n", encoding="utf-8")  # ingested version unavailable
    (vault / "a.md").write_text("v2\n", encoding="utf-8")
    (vault / "b.md").write_text("b2\n", encoding="utf-8")

    take_snapshot(brain, vault, None, BATCH, TODAY)

    items = {i.origin: i for i in load_batches(brain)[0].items}
    assert (brain / items["vault:a.md"].previous).read_bytes() == b"v1\n"
    assert items["vault:b.md"].status == "changed" and items["vault:b.md"].previous is None


def test_ingested_source_unchanged(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = _brain(tmp_path)
    take_snapshot(brain, vault, None, BATCH, TODAY)
    _mark_ingested(brain, "vault:a.md")
    _lose_batches(brain)

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.unchanged == ["vault:a.md"] and load_batches(brain) == []


def test_ignored_attachments_not_copied(tmp_path: Path, git_vault: Callable[[Files], Path]) -> None:
    vault = git_vault(
        {
            **OBSIDIAN,
            ".gitignore": "secret/\n",
            "a.md": "![[key.png]] ![k](secret/other.png) ![[ok.png]]\n",
            "secret/key.png": b"k",
            "secret/other.png": b"o",
            "ok.png": b"ok",
        }
    )
    brain = _brain(tmp_path)

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.attachments == 1
    assert _snapshot_files(brain) == {"raw/vault/a.md", "raw/vault/ok.png"}


def test_brain_files_not_copied_as_attachments(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "![[logo.png]] ![l](second-brain/raw/x/pic.png)\n"})
    brain = vault / "second-brain"
    init_brain(brain)
    (brain / "logo.png").write_bytes(b"l")
    (brain / "raw/x").mkdir()
    (brain / "raw/x/pic.png").write_bytes(b"p")

    result = take_snapshot(brain, vault, None, BATCH, TODAY)

    assert result.attachments == 0


def test_single_note_in_brain_rejected(tmp_path: Path, make_vault: Callable[[Files], Path]) -> None:
    vault = make_vault({**OBSIDIAN, "a.md": "a\n"})
    brain = vault / "second-brain"
    init_brain(brain)
    (brain / "concepts/x.md").write_text("x\n", encoding="utf-8")
    other = vault / "old-brain"
    init_brain(other)
    (other / "raw/s").mkdir()
    (other / "raw/s/n.md").write_text("n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the brain"):
        take_snapshot(brain, brain / "concepts/x.md", None, BATCH, TODAY)
    with pytest.raises(ValueError, match="raw"):
        take_snapshot(brain, other / "raw/s/n.md", None, BATCH, TODAY)
    assert not (brain / ".okf/sources.json").exists()


def test_single_note_ignored_rejected(tmp_path: Path, git_vault: Callable[[Files], Path]) -> None:
    vault = git_vault({**OBSIDIAN, ".gitignore": "private/\n", "private/s.md": "s\n"})
    brain = _brain(tmp_path)

    with pytest.raises(ValueError, match="git-ignored"):
        take_snapshot(brain, vault / "private/s.md", None, BATCH, TODAY)
