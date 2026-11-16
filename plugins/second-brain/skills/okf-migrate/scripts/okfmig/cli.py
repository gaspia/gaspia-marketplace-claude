"""Command-line interface for okf-migrate."""

import argparse
import sys
from pathlib import Path

COMMANDS = {
    "scan": "Scan a vault and report what would change",
    "plan": "Produce a migration plan for a vault",
    "apply": "Apply the migration plan to a vault",
    "validate": "Validate a vault against the Open Knowledge Format",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okf_migrate", description="Migrate an Obsidian vault to OKF")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("vault", type=Path, help="path to the vault directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.vault.is_dir():
        print(f"error: vault is not a directory: {args.vault}", file=sys.stderr)
        return 2
    return 0
