"""Command-line interface for the second brain ingest skill."""

import argparse
import sys
from datetime import date
from pathlib import Path

from okfmig import gitutil
from okfmig.brain import init_brain, load_settings, outer_vault
from okfmig.brainvalidate import validate_brain
from okfmig.ingest import BatchError, complete_batch, similar_pages
from okfmig.snapshot import load_batches, take_snapshot

COMMANDS = {
    "init": "Create or update the brain layout, settings, type rules and schema",
    "snapshot": "Copy new and changed sources into raw/ and queue them in batches",
    "next": "Print the next pending batch",
    "similar": "List existing wiki pages close to a title",
    "done": "Mark a batch ingested: normalize pages, update the ledger and log",
    "validate": "Validate the brain: OKF checks plus the brain rules",
}


class _Exit(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okf_brain", description="Ingest notes into an OKF second brain")
    sub = parser.add_subparsers(dest="command", required=True)
    parsers = {}
    for name, help_text in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("brain", type=Path, help="path to the brain directory")
        parsers[name] = p
    parsers["snapshot"].add_argument("source", type=Path, help="source vault, folder or single .md note")
    parsers["snapshot"].add_argument("--name", help="source name (default: source folder name in kebab-case)")
    parsers["similar"].add_argument("title", help="title of the page you are about to create")
    parsers["done"].add_argument("batch_id", help="batch id, as printed by next")
    for name in ("snapshot", "done"):
        parsers[name].add_argument(
            "--today", type=date.fromisoformat, default=None, help="date to record, YYYY-MM-DD (default: today)"
        )
    return parser


def _today(args: argparse.Namespace) -> date:
    return args.today or date.today()


def _init(args: argparse.Namespace) -> int:
    brain = args.brain
    changed = init_brain(brain)
    for path in changed:
        print(path)
    print(f"{len(changed)} paths created or changed in {brain}")
    outer = outer_vault(brain)
    if outer is not None:
        rel = brain.relative_to(outer).as_posix()
        print(
            f'warning: this brain is inside the Obsidian vault {outer}; add "{rel}/**" to that vault\'s '
            ".okf/types.yaml exclude, or move the brain into its own vault.",
            file=sys.stderr,
        )
    return 0


def _snapshot(args: argparse.Namespace) -> int:
    brain = args.brain
    if gitutil.repo_location(brain) is None:
        raise _Exit(2, f"error: {brain} is not inside a git repository")
    settings = load_settings(brain)
    if load_batches(brain):
        raise _Exit(1, "pending batches exist; run next/done first")
    dirty = gitutil.dirty_under(brain)  # `.obsidian/` and `.okf/` (ledger, settings) never block
    if dirty:
        lines = "\n".join(f"  {p}" for p in dirty)
        raise _Exit(2, f"error: uncommitted changes in the brain (commit or stash them first):\n{lines}")
    result = take_snapshot(brain, args.source, args.name, settings, _today(args))
    print(f"Source: {result.name}")
    print(
        f"new: {len(result.new)}, changed: {len(result.changed)}, unchanged: {len(result.unchanged)}, "
        f"attachments copied: {result.attachments}"
    )
    if result.missing:
        print(f"Origins no longer found ({len(result.missing)}; kept in the ledger):")
        for origin in result.missing:
            print(f"  {origin}")
    print(f"Batches: {', '.join(result.batches) if result.batches else 'none'}")
    return 0


def _next(args: argparse.Namespace) -> int:
    batches = load_batches(args.brain)
    if not batches:
        print("no pending batches")
        return 0
    batch = batches[0]
    print(f"batch {batch.id} ({len(batch.items)} sources, {len(batches)} batches pending)")
    for item in batch.items:
        print(f"- origin: {item.origin}")
        print(f"  raw: {item.raw_path}")
        print(f"  status: {item.status}")
        if item.status == "changed":
            print(f"  previous: {item.previous or 'none kept (read the whole snapshot)'}")
        print(f"  summary page: {item.summary_page}")
    return 0


def _similar(args: argparse.Namespace) -> int:
    hits = similar_pages(args.brain, args.title)
    if not hits:
        print("no similar pages")
    for path, title, score in hits:
        print(f"{score:.2f}  {path}  {title}")
    return 0


def _done(args: argparse.Namespace) -> int:
    try:
        lines = complete_batch(args.brain, args.batch_id, _today(args))
    except BatchError as exc:
        raise _Exit(1, f"refused: {exc}") from exc
    for line in lines:
        print(line)
    print(f"Batch {args.batch_id} done.")
    return 0


def _validate(args: argparse.Namespace) -> int:
    report = validate_brain(args.brain)
    for e in report.errors:
        print(f"error: {e}")
    for w in report.warnings:
        print(f"warning: {w}")
    print(f"{len(report.errors)} errors, {len(report.warnings)} warnings")
    return 1 if report.errors else 0


HANDLERS = {
    "init": _init,
    "snapshot": _snapshot,
    "next": _next,
    "similar": _similar,
    "done": _done,
    "validate": _validate,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # on-disk letter case: git paths and the brain-inside-source checks compare spellings
    args.brain = gitutil.canonical(args.brain)
    if args.command != "init" and not args.brain.is_dir():
        print(f"error: brain is not a directory: {args.brain}", file=sys.stderr)
        return 2
    if args.command == "snapshot":
        args.source = gitutil.canonical(args.source)
    try:
        return HANDLERS[args.command](args)
    except _Exit as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
