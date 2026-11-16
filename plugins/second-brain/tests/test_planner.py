from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

import pytest
from okfmig.planner import (
    Plan,
    build_inventory,
    build_plan,
    plan_from_json,
    plan_to_json,
    render_inventory_summary,
    render_plan_md,
)
from okfmig.typerules import Rule, TypeConfig
from okfmig.vault import discover

TODAY = date(2026, 11, 16)
STAMP = "2026-11-01T10:00:00+00:00"

RAW = {
    "a.md": "Hello, see [[b]].\n",
    "b.md": "---\nnotion-id: 1\n---\nB body #project\n",
    "Work/c.md": "Met on [[2026-11-09]].\n",
}


def _stamps(files: dict[str, str]) -> dict[PurePosixPath, str]:
    return {PurePosixPath(p): STAMP for p in files}


def _plan(root: Path, cfg: TypeConfig, files: dict[str, str] | None = None) -> Plan:
    vault = discover(root, cfg.exclude)
    return build_plan(vault, cfg, _stamps(files or {}), TODAY)


def _apply(root: Path, plan: Plan) -> None:
    for op in plan.ops:
        if op.action == "rename":
            assert op.new_path is not None
            (root / op.path).unlink()
            target = root / op.new_path
        else:
            target = root / op.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(op.new_content.encode("utf-8"))


def _ops(plan: Plan) -> dict[str, object]:
    return {op.path: op for op in plan.ops if op.action != "rename"}


def test_raw_vault_plan(make_vault: Callable) -> None:
    root = make_vault(RAW)
    plan = _plan(root, TypeConfig(default="note"), RAW)

    ops = _ops(plan)
    assert sorted(ops) == ["Work/c.md", "Work/index.md", "a.md", "b.md", "index.md", "log.md"]
    assert [op.path for op in plan.ops] == sorted(op.path for op in plan.ops)
    assert ops["index.md"].action == "create"
    assert ops["index.md"].old_sha256 is None
    assert ops["index.md"].new_content.startswith('---\nokf_version: "0.2"\n---\n\n# vault\n')
    assert "* [Work](Work/index.md)" in ops["index.md"].new_content
    assert ops["Work/index.md"].action == "create"
    assert "* [c](c.md)" in ops["Work/index.md"].new_content
    assert ops["log.md"].action == "create"
    assert (
        "* **Init**: Migrated vault to OKF (3 notes typed, 1 links converted, 2 indexes created)."
        in ops["log.md"].new_content
    )
    assert "## 2026-11-16" in ops["log.md"].new_content
    assert ops["b.md"].action == "modify"
    assert ops["b.md"].old_sha256 is not None
    assert ops["b.md"].new_content.startswith("---\nnotion-id: 1\n")
    assert "type: note" in ops["b.md"].new_content
    assert ops["a.md"].new_content.endswith("Hello, see [b](b.md).\n")
    assert ops["Work/c.md"].new_content.endswith("Met on 2026-11-09.\n")
    assert len(plan.dates_unlinked) == 1
    assert plan.blocking == []
    assert plan.counts == {
        "notes_typed": 3,
        "links_converted": 1,
        "indexes_created": 2,
        "indexes_updated": 0,
        "dates_unlinked": 1,
    }
    assert plan.types_assigned["a.md"] == {"type": "note", "reason": "default"}


def test_unclassified_blocks(make_vault: Callable) -> None:
    root = make_vault(RAW)
    plan = _plan(root, TypeConfig(), RAW)
    assert plan.unclassified == ["Work/c.md", "a.md", "b.md"]
    assert "a.md: unclassified" in plan.blocking


def test_invalid_frontmatter_blocks(make_vault: Callable) -> None:
    files = {"a.md": "---\nfoo: [unclosed\n---\nbody\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert any(b.startswith("a.md: invalid frontmatter: ") for b in plan.blocking)
    assert "a.md" not in {op.path for op in plan.ops}


def test_unfillable_empty_key_blocks(make_vault: Callable) -> None:
    files = {"a.md": "---\n{type: null, x: 1}\n---\nbody\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert any(b.startswith("a.md: cannot fill") for b in plan.blocking)
    assert "a.md" not in {op.path for op in plan.ops}


def test_undecodable_blocks(make_vault: Callable) -> None:
    root = make_vault({"a.md": b"\xff\xfe bad", "b.md": "ok\n"})
    plan = _plan(root, TypeConfig(default="note"))
    assert "a.md: not valid UTF-8" in plan.blocking


def test_reserved_note_renamed_and_links_updated(make_vault: Callable) -> None:
    files = {"Work/index.md": "Just some prose about work.\n", "a.md": "See [[Work/index]].\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    renames = [op for op in plan.ops if op.action == "rename"]
    assert len(renames) == 1
    assert renames[0].path == "Work/index.md"
    assert renames[0].new_path == "Work/work-index.md"
    assert plan.renames == {"Work/index.md": "Work/work-index.md"}
    ops = _ops(plan)
    assert "[Work/index](Work/work-index.md)" in ops["a.md"].new_content
    assert ops["Work/index.md"].action == "create"
    assert "* [work-index](work-index.md)" in ops["Work/index.md"].new_content


def test_reserved_rename_blocked_when_target_exists(make_vault: Callable) -> None:
    files = {"Work/index.md": "Just prose.\n", "Work/Work-Index.md": "other\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert any(b.startswith("Work/index.md: ") for b in plan.blocking)
    assert plan.renames == {}


def test_reserved_override(make_vault: Callable) -> None:
    files = {"Work/index.md": "Just some prose about work.\n", "a.md": "See [[Work/index]].\n"}
    root = make_vault(files)
    cfg = TypeConfig(default="note", reserved={"Work/index.md": "index"})
    plan = _plan(root, cfg, files)
    assert not [op for op in plan.ops if op.action == "rename"]
    assert plan.renames == {}
    ops = _ops(plan)
    assert "[Work/index](Work/index.md)" in ops["a.md"].new_content
    assert "Work/index.md" not in ops  # curated index, nothing unlisted in Work, so left untouched


def test_curated_index_gets_unlisted_and_converted(make_vault: Callable) -> None:
    files = {
        "a.md": "A\n",
        "b.md": "B\n",
        "index.md": "# Home\n\n- [[a]]\n",
    }
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    content = _ops(plan)["index.md"].new_content
    assert content.startswith('---\nokf_version: "0.2"\n---\n\n# Home\n\n- [a](a.md)\n')
    assert "# Unlisted\n\n* [b](b.md)\n" in content
    assert "* [a](a.md)" not in content
    assert plan.counts["indexes_updated"] == 1


def test_frontmatter_wikilinks_reported_not_changed(make_vault: Callable) -> None:
    files = {"a.md": '---\nrelated: "[[b]]"\n---\nbody\n', "b.md": "B\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert plan.frontmatter_wikilinks == [{"file": "a.md", "link": "[[b]]"}]
    assert 'related: "[[b]]"' in _ops(plan)["a.md"].new_content


def test_nonkebab_existing_type_kept_and_reported(make_vault: Callable) -> None:
    files = {"a.md": "---\ntype: Meeting Notes\ntitle: A\ntimestamp: x\n---\nbody\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert plan.nonkebab_types == [{"file": "a.md", "type": "Meeting Notes"}]
    assert "a.md" not in plan.types_assigned
    assert "a.md" not in _ops(plan)


def test_rule_type_reason(make_vault: Callable) -> None:
    root = make_vault(RAW)
    cfg = TypeConfig(default="note", rules=[Rule("tag", "project", "project")])
    plan = _plan(root, cfg, RAW)
    assert plan.types_assigned["b.md"] == {"type": "project", "reason": "rule 1: tag project"}


def test_idempotent_after_apply(make_vault: Callable) -> None:
    root = make_vault(RAW)
    cfg = TypeConfig(default="note")
    _apply(root, _plan(root, cfg, RAW))
    again = _plan(root, cfg, RAW)
    assert again.ops == []
    assert again.blocking == []


def test_rerun_new_note(make_vault: Callable) -> None:
    root = make_vault(RAW)
    cfg = TypeConfig(default="note")
    _apply(root, _plan(root, cfg, RAW))
    (root / "Work" / "d.md").write_text("New note\n", encoding="utf-8")
    plan = _plan(root, cfg, {**RAW, "Work/d.md": ""})
    assert sorted(op.path for op in plan.ops) == ["Work/d.md", "Work/index.md", "log.md"]
    log = _ops(plan)["log.md"].new_content
    assert log.count("## 2026-11-16") == 1
    assert "* **Update**: Brought 1 notes to OKF (0 links converted, 1 indexes updated)." in log
    init = log.index("* **Init**")
    assert log.index("## 2026-11-16") < init < log.index("* **Update**")


def test_plan_json_round_trip(make_vault: Callable) -> None:
    files = {**RAW, "Work/index.md": "Prose only.\n", "x.md": "[[missing]] [[Work/index]]\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert any(op.action == "rename" for op in plan.ops)
    restored = plan_from_json(plan_to_json(plan))
    assert restored == plan


def test_render_plan_md(make_vault: Callable) -> None:
    files = {**RAW, "x.md": "[[missing]]\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    md = render_plan_md(plan)
    for section in (
        "Summary",
        "Blocking",
        "Unclassified",
        "Renames",
        "Types assigned",
        "Ambiguous links",
        "Unresolved links",
        "Dates unlinked",
        "Frontmatter wikilinks (not changed)",
        "Non-kebab existing types (informational)",
        "Indexes",
        "Excluded (outside bundle)",
        "Files changed",
    ):
        assert f"## {section}" in md
    assert "a.md: [[b]] -> [b](b.md)" not in md  # resolved links are not itemized as problems
    assert "x.md: [[missing]] -> [missing](missing.md)" in md
    assert "Work/c.md: [[2026-11-09]] -> 2026-11-09" in md
    assert "—" not in md


@pytest.mark.parametrize("needle", ["Notes: 3", "project"])
def test_inventory_summary_mentions_counts(make_vault: Callable, needle: str) -> None:
    root = make_vault(RAW)
    cfg = TypeConfig()
    inv = build_inventory(discover(root), cfg)
    assert inv["notes"] == 3
    summary = render_inventory_summary(inv)
    assert needle in summary
    assert "—" not in summary


def test_case_variant_index_note_blocks_create(make_vault: Callable) -> None:
    files = {"a.md": "A\n", "Work/Index.md": "Work prose\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert "Work/index.md: conflicts with existing Work/Index.md (case-insensitive)" in plan.blocking
    assert not [op for op in plan.ops if op.path == "Work/index.md"]
    assert "Work/Index.md" in _ops(plan)


def test_case_variant_root_log_blocks_create(make_vault: Callable) -> None:
    files = {"a.md": "A\n", "Log.md": "Some prose\n"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert "log.md: conflicts with existing Log.md (case-insensitive)" in plan.blocking
    assert not [op for op in plan.ops if op.path == "log.md"]


def test_noteless_folder_index_is_stale_not_touched(make_vault: Callable) -> None:
    root = make_vault(RAW)
    cfg = TypeConfig(default="note")
    _apply(root, _plan(root, cfg, RAW))
    (root / "Work" / "c.md").unlink()
    plan = _plan(root, cfg, RAW)
    assert "Work/index.md" not in {op.path for op in plan.ops}
    assert plan.stale_indexes == ["Work/index.md"]
    assert "Work/index.md" in render_plan_md(plan)
    assert "## Stale generated indexes (delete by hand)" in render_plan_md(plan)
    root_index = _ops(plan)["index.md"].new_content
    assert "Work/index.md" not in root_index
    assert "# Subfolders" not in root_index


def test_curated_index_in_attachments_only_folder_untouched(make_vault: Callable) -> None:
    files = {"a.md": "A\n", "A/B/index.md": "---\nx: 1\n---\n- [[pic.png]]\n", "A/B/pic.png": "png"}
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    paths = {op.path for op in plan.ops}
    assert not {"A/B/index.md", "A/index.md"} & paths
    assert plan.stale_indexes == []
    assert "# Subfolders" not in _ops(plan)["index.md"].new_content


def test_preexisting_broken_links_recorded(make_vault: Callable) -> None:
    files = {
        "a.md": "---\ntype: note\n---\nOld [gone](Gone%20Note.md#H) and [ok](b.md) and `[c](Code.md)` [w](https://x.y).\n",
        "b.md": "[[Never]]\n",
        "Work/index.md": "# Work\n\n- [x](missing.md)\n",
    }
    root = make_vault(files)
    plan = _plan(root, TypeConfig(default="note"), files)
    assert plan.preexisting_broken == [
        {"file": "Work/index.md", "target": "Work/missing.md"},
        {"file": "a.md", "target": "Gone Note.md"},
    ]
    assert "- a.md: Gone Note.md" in render_plan_md(plan)
    assert plan_from_json(plan_to_json(plan)) == plan


@pytest.mark.parametrize(
    ("body", "title"),
    [
        ("intro\n\n# Later heading\n", "Note"),
        ("```bash\n# a comment\n```\n", "Note"),
        ("# **Simplified 1:1 (O2O) **\n\ntext\n", "Simplified 1:1 (O2O)"),
        ("\n# Real Title\n\ntext\n", "Real Title"),
    ],
)
def test_title_from_leading_h1_else_stem(make_vault: Callable, body: str, title: str) -> None:
    root = make_vault({"Note.md": body})
    plan = _plan(root, TypeConfig(default="note"), {"Note.md": body})
    op = next(o for o in plan.ops if o.path == "Note.md")
    assert f"title: {title}\n" in op.new_content or f"title: '{title}'\n" in op.new_content
