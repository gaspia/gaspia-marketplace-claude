"""Command-line interface for okf-migrate."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path, PurePosixPath

from okfmig import gitutil, typerules
from okfmig.apply import BlockedPlanError, StaleFileError, apply_plan
from okfmig.planner import (
    Plan,
    build_inventory,
    build_plan,
    plan_from_json,
    plan_to_json,
    render_inventory_summary,
    render_plan_md,
)
from okfmig.typerules import TypeConfig
from okfmig.validate import expected_unresolved_from, validate
from okfmig.vault import discover

COMMANDS = {
    "scan": "Scan a vault and report what would change",
    "plan": "Produce a migration plan for a vault",
    "apply": "Apply the migration plan to a vault",
    "validate": "Validate a vault against the Open Knowledge Format",
}


class _Exit(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okf_migrate", description="Migrate an Obsidian vault to OKF")
    sub = parser.add_subparsers(dest="command", required=True)
    parsers = {}
    for name, help_text in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("vault", type=Path, help="path to the vault directory")
        parsers[name] = p
    for name in ("scan", "plan", "apply"):
        parsers[name].add_argument("--workdir", type=Path, help="work directory (default: <vault>/.okf-migrate)")
    for name in ("plan", "validate"):
        parsers[name].add_argument("--types", type=Path, help="type rules (default: <vault>/.okf/types.yaml)")
    for name in ("apply", "validate"):
        parsers[name].add_argument("--plan", type=Path, help="plan file (default: <workdir>/plan.json)")
    parsers["plan"].add_argument(
        "--today", type=date.fromisoformat, default=None, help="date for log entries, YYYY-MM-DD (default: today)"
    )
    return parser


def _workdir(args: argparse.Namespace) -> Path:
    workdir = getattr(args, "workdir", None)
    return workdir if workdir is not None else args.vault / ".okf-migrate"


def _load_types(path: Path) -> TypeConfig:
    try:
        return typerules.load(path)
    except ValueError as exc:
        raise _Exit(2, f"error: invalid type rules: {exc}") from exc


def _require_repo(vault: Path) -> None:
    location = gitutil.repo_location(vault)
    if location is None:
        raise _Exit(2, f"error: {vault} is not inside a git repository")
    top, prefix = location
    entry = ".okf-migrate/" if prefix == PurePosixPath(".") else f"{prefix}/.okf-migrate/"
    gitutil.ensure_info_exclude(top, entry)


def _require_clean(vault: Path) -> None:
    dirty = gitutil.dirty_under(vault)
    if dirty:
        lines = "\n".join(f"  {p}" for p in dirty)
        raise _Exit(2, f"error: uncommitted changes (commit or stash them first):\n{lines}")


def _ignored(vault: Path) -> list[PurePosixPath]:
    """Git-ignored paths: `git restore` cannot undo changes there, so they stay outside the bundle."""
    return gitutil.ignored_under(vault)


def _timestamps(vault: Path) -> dict:
    return gitutil.commit_times_under(vault)  # {} without commits: the planner falls back to file mtimes


def _scan(args: argparse.Namespace) -> int:
    vault = args.vault
    _require_repo(vault)
    _require_clean(vault)
    cfg = _load_types(vault / ".okf" / "types.yaml")
    inventory = build_inventory(discover(vault, cfg.exclude, _ignored(vault)), cfg)
    workdir = _workdir(args)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "inventory.json"
    out.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(render_inventory_summary(inventory), end="")
    print(f"Inventory written to {out}")
    return 0


def _plan(args: argparse.Namespace) -> int:
    vault = args.vault
    _require_repo(vault)
    cfg = _load_types(args.types if args.types is not None else vault / ".okf" / "types.yaml")
    plan = build_plan(
        discover(vault, cfg.exclude, _ignored(vault)), cfg, _timestamps(vault), args.today or date.today()
    )
    workdir = _workdir(args)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "plan.json").write_text(plan_to_json(plan) + "\n", encoding="utf-8")
    plan_md = workdir / "plan.md"
    plan_md.write_text(render_plan_md(plan), encoding="utf-8")
    c = plan.counts
    print(f"Files changed: {len(plan.ops)}")
    print(f"Notes typed: {c['notes_typed']}")
    print(f"Links converted: {c['links_converted']}, dates unlinked: {c['dates_unlinked']}")
    print(f"Indexes created: {c['indexes_created']}, indexes updated: {c['indexes_updated']}")
    print(f"Renames: {len(plan.renames)}, unclassified: {len(plan.unclassified)}")
    print(f"Ambiguous links: {len(plan.ambiguous)}, unresolved links: {len(plan.unresolved)}")
    print(f"Blocking items: {len(plan.blocking)}")
    for item in plan.blocking:
        print(f"  {item}")
    print(f"Plan written to {plan_md}")
    return 0


def _plan_path(args: argparse.Namespace) -> Path:
    return args.plan if args.plan is not None else _workdir(args) / "plan.json"


def _read_plan(path: Path) -> Plan:
    try:
        return plan_from_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _Exit(2, f"error: cannot read plan {path}: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise _Exit(2, f"error: invalid plan {path}: {exc}") from exc


def _apply(args: argparse.Namespace) -> int:
    vault = args.vault
    _require_repo(vault)
    _require_clean(vault)
    plan = _read_plan(_plan_path(args))
    try:
        changed = apply_plan(vault, plan)
    except (BlockedPlanError, StaleFileError) as exc:
        print(f"refused: {exc}")
        return 1
    for line in changed:
        print(line)
    print(f"Applied {len(changed)} changes.")
    return 0


def _validate(args: argparse.Namespace) -> int:
    vault = args.vault
    cfg = _load_types(args.types if args.types is not None else vault / ".okf" / "types.yaml")
    expected = None
    if args.plan is not None:
        expected = expected_unresolved_from(_read_plan(args.plan))
    report = validate(vault, cfg, expected, _ignored(vault))
    for e in report.errors:
        print(f"error: {e}")
    for w in report.warnings:
        print(f"warning: {w}")
    print(f"{len(report.errors)} errors, {len(report.warnings)} warnings")
    return 1 if report.errors else 0


HANDLERS = {"scan": _scan, "plan": _plan, "apply": _apply, "validate": _validate}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    args.vault = args.vault.resolve()  # `.` would otherwise give an empty root index title
    if not args.vault.is_dir():
        print(f"error: vault is not a directory: {args.vault}", file=sys.stderr)
        return 2
    try:
        return HANDLERS[args.command](args)
    except _Exit as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
