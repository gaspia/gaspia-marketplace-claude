import os
import stat
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

import pytest
from okfmig.apply import BlockedPlanError, StaleFileError, apply_plan
from okfmig.planner import Plan, build_plan
from okfmig.typerules import TypeConfig
from okfmig.validate import validate
from okfmig.vault import discover

TODAY = date(2026, 11, 16)
STAMP = "2026-11-01T10:00:00+00:00"
CFG = TypeConfig(default="note")


def _plan(root: Path, cfg: TypeConfig = CFG) -> Plan:
    vault = discover(root, cfg.exclude)
    stamps = {n.path: STAMP for n in [*vault.notes, *vault.reserved]}
    return build_plan(vault, cfg, stamps, TODAY)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_apply_refuses_blocking(make_vault: Callable) -> None:
    root = make_vault({"a.md": "---\ntitle: [unclosed\n---\nBody\n", "b.md": "B\n"})
    plan = _plan(root)
    assert plan.blocking
    before = _snapshot(root)
    with pytest.raises(BlockedPlanError):
        apply_plan(root, plan)
    assert _snapshot(root) == before


def test_apply_refuses_stale(make_vault: Callable) -> None:
    root = make_vault({"a.md": "A\n", "b.md": "B\n"})
    plan = _plan(root)
    (root / "b.md").write_text("B changed\n", encoding="utf-8")
    before = _snapshot(root)
    with pytest.raises(StaleFileError, match=r"b\.md"):
        apply_plan(root, plan)
    assert _snapshot(root) == before


def test_apply_refuses_create_over_new_file(make_vault: Callable) -> None:
    root = make_vault({"a.md": "A\n"})
    plan = _plan(root)
    assert any(op.path == "index.md" and op.action == "create" for op in plan.ops)
    (root / "index.md").write_text("someone made this\n", encoding="utf-8")
    before = _snapshot(root)
    with pytest.raises(StaleFileError, match=r"index\.md"):
        apply_plan(root, plan)
    assert _snapshot(root) == before


def test_apply_rename_and_crlf_preserved(make_vault: Callable) -> None:
    note_index = "# Work home\r\n\r\nPlain prose, no links.\r\n"
    crlf = "﻿Line one\r\nLine two\r\n"
    root = make_vault({"Work/index.md": note_index, "Work/a.md": crlf})
    plan = _plan(root)
    assert plan.blocking == []
    actions = [(op.path, op.action) for op in plan.ops if op.path == "Work/index.md"]
    assert actions == [("Work/index.md", "rename"), ("Work/index.md", "create")]

    changed = apply_plan(root, plan)

    assert "Work/work-index.md" in "\n".join(changed)
    renamed = (root / "Work/work-index.md").read_bytes()
    assert renamed.endswith(b"# Work home\r\n\r\nPlain prose, no links.\r\n")
    assert b"\r\ntype: note\r\n" in renamed
    generated = (root / "Work/index.md").read_text(encoding="utf-8")
    assert "* [Work home](work-index.md)" in generated
    assert "<!-- okf-migrate:generated -->" in generated
    a = (root / "Work/a.md").read_bytes()
    assert a.startswith("﻿---\r\n".encode())
    assert a.endswith(b"---\r\nLine one\r\nLine two\r\n")
    ops = {op.path: op for op in plan.ops if op.action != "rename"}
    assert a == ops["Work/a.md"].new_content.encode("utf-8")
    assert not list(root.rglob("*.tmp"))
    assert _plan(root).ops == []


def _migrated(make_vault: Callable) -> Path:
    root = make_vault(
        {
            "a.md": "See [[b]] and [[Gone]].\n",
            "b.md": "---\ntype: note\n---\nB\n",
            "Work/c.md": "C\n",
            "CLAUDE.md": "[[not checked]]\n",
        }
    )
    apply_plan(root, _plan(root))
    return root


def test_validate_conformant_and_failures(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    report = validate(root, CFG)
    assert report.errors == []
    assert "outside bundle: CLAUDE.md" in report.warnings

    (root / "x.md").write_text("no frontmatter\n", encoding="utf-8")
    (root / "Work/c.md").write_text((root / "Work/c.md").read_text(encoding="utf-8") + "Now [[y]].\n")
    work_index = (root / "Work/index.md").read_text(encoding="utf-8")
    (root / "Work/index.md").write_text("---\ntitle: Work\n---\n" + work_index, encoding="utf-8")
    log = (root / "log.md").read_text(encoding="utf-8")
    (root / "log.md").write_text("---\ntitle: Log\n---\n" + log, encoding="utf-8")
    (root / "e.md").write_text("---\ntype: ''\n---\n", encoding="utf-8")
    (root / "bad.md").write_bytes(b"\xff\xfe bad")

    errors = validate(root, CFG).errors
    failing = {e.split(": ", 1)[0] for e in errors}
    assert failing == {"x.md", "Work/c.md", "Work/index.md", "log.md", "e.md", "bad.md"}
    assert any(e.startswith("Work/c.md") and "[[y]]" in e for e in errors)


def test_validate_root_index_frontmatter(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    text = (root / "index.md").read_text(encoding="utf-8")
    (root / "index.md").write_text(text.replace('okf_version: "0.2"\n', 'okf_version: "0.2"\ntitle: Root\n'))
    assert _failing(validate(root, CFG).errors) == {"index.md"}


def _failing(errors: list[str]) -> set[str]:
    return {e.split(": ", 1)[0] for e in errors}


def test_validate_missing_root_index(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    (root / "index.md").unlink()
    assert _failing(validate(root, CFG).errors) == {"index.md"}


def test_validate_root_index_without_frontmatter(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    text = (root / "index.md").read_text(encoding="utf-8")
    (root / "index.md").write_text(text.replace('---\nokf_version: "0.2"\n---\n\n', ""), encoding="utf-8")
    assert not (root / "index.md").read_text(encoding="utf-8").startswith("---")
    assert _failing(validate(root, CFG).errors) == {"index.md"}


@pytest.mark.parametrize("value", ['"0.1"', "0.2", "'0.20'"])
def test_validate_root_index_wrong_version(make_vault: Callable, value: str) -> None:
    root = _migrated(make_vault)
    text = (root / "index.md").read_text(encoding="utf-8")
    (root / "index.md").write_text(text.replace('okf_version: "0.2"', f"okf_version: {value}"), encoding="utf-8")
    assert _failing(validate(root, CFG).errors) == {"index.md"}


def test_validate_empty_vault_needs_no_root_index(make_vault: Callable) -> None:
    root = make_vault({"CLAUDE.md": "rules\n"})
    assert validate(root, CFG).errors == []


def test_validate_frontmatter_wikilinks_and_code_are_allowed(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    (root / "f.md").write_text(
        "---\ntype: note\nrelated: '[[a]]'\n---\nCode `[[a]]` and\n\n```\n[[a]]\n```\n", encoding="utf-8"
    )
    assert validate(root, CFG).errors == []


def test_validate_unresolved_expected(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    plan_unresolved = {str(PurePosixPath("Gone.md"))}

    report = validate(root, CFG)
    assert report.errors == []
    assert any("Gone.md" in w for w in report.warnings)

    assert validate(root, CFG, expected_unresolved=plan_unresolved).errors == []

    errors = validate(root, CFG, expected_unresolved=set()).errors
    assert any("a.md" in e and "Gone.md" in e for e in errors)


def test_validate_ignores_external_and_anchor_links(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    (root / "g.md").write_text(
        "---\ntype: note\n---\n[w](https://x.y/z) [m](mailto:a@b.c) [h](#Top) [b](b.md#Head) [s](Work/c.md)\n",
        encoding="utf-8",
    )
    assert validate(root, CFG, expected_unresolved={"Gone.md"}).errors == []


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


@pytest.mark.parametrize("mode", [0o644, 0o755, 0o600])
def test_apply_keeps_mode_on_modify(make_vault: Callable, mode: int) -> None:
    root = make_vault({"a.md": "A\n"})
    (root / "a.md").chmod(mode)
    plan = _plan(root)
    assert any(op.path == "a.md" and op.action == "modify" for op in plan.ops)
    apply_plan(root, plan)
    assert _mode(root / "a.md") == mode


def test_apply_rename_keeps_source_mode(make_vault: Callable) -> None:
    root = make_vault({"Work/index.md": "# Home\n\nProse.\n"})
    (root / "Work/index.md").chmod(0o640)
    plan = _plan(root)
    assert plan.renames == {"Work/index.md": "Work/work-index.md"}
    apply_plan(root, plan)
    assert _mode(root / "Work/work-index.md") == 0o640


def test_apply_create_uses_umask(make_vault: Callable) -> None:
    root = make_vault({"a.md": "A\n"})
    plan = _plan(root)
    old = os.umask(0o027)
    try:
        apply_plan(root, plan)
    finally:
        os.umask(old)
    assert _mode(root / "index.md") == 0o640
    assert _mode(root / "log.md") == 0o640
    assert _umask() == old


def test_validate_reports_links_escaping_the_vault(make_vault: Callable) -> None:
    root = _migrated(make_vault)
    (root / "h.md").write_text("---\ntype: note\n---\n[up](../outside.md)\n", encoding="utf-8")
    errors = validate(root, CFG, expected_unresolved={"Gone.md"}).errors
    assert errors == ["h.md: link escapes the vault: ../outside.md"]
