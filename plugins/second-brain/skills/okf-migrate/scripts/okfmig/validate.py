"""OKF conformance check for a migrated vault."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from okfmig.frontmatter import is_empty_value, parse
from okfmig.links import linked_paths, wikilinks_in
from okfmig.planner import Plan
from okfmig.reserved import OKF_VERSION
from okfmig.typerules import TypeConfig
from okfmig.vault import Note, discover

ROOT_INDEX = PurePosixPath("index.md")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def expected_unresolved_from(plan: Plan) -> set[str]:
    """Vault paths a plan expects to stay broken: converted unresolved links plus links already broken."""
    targets = {item["target"] for item in plan.preexisting_broken}
    for item in plan.unresolved:
        targets.update(str(p) for p in linked_paths(item["after"], PurePosixPath(item["file"])))
    return targets


def _check_note(note: Note, errors: list[str]) -> str | None:
    """Check frontmatter of a regular note; return its body, or None if it cannot be parsed."""
    parsed = parse(note.text)
    if not parsed.has_block:
        errors.append(f"{note.path}: no frontmatter")
    elif parsed.error is not None:
        errors.append(f"{note.path}: invalid frontmatter: {parsed.error}")
        return None
    elif is_empty_value((parsed.data or {}).get("type")):
        errors.append(f"{note.path}: frontmatter has no type")
    return parsed.body


def _check_reserved(note: Note, errors: list[str]) -> str:
    parsed = parse(note.text)
    if note.path == ROOT_INDEX:
        if parsed.error is not None or parsed.data != {"okf_version": OKF_VERSION}:
            errors.append(f'{note.path}: root index frontmatter must be exactly okf_version: "{OKF_VERSION}"')
    elif parsed.has_block:
        errors.append(f"{note.path}: {note.path.name} must not have frontmatter")
    return parsed.body


def validate(
    root: Path,
    cfg: TypeConfig,
    expected_unresolved: set[str] | None = None,
    ignored: Iterable[PurePosixPath] = (),
) -> Report:
    vault = discover(root, cfg.exclude, ignored)
    report = Report()
    for path in vault.undecodable:
        report.errors.append(f"{path}: not valid UTF-8")
    bodies: list[tuple[Note, str]] = []
    for note in vault.notes:
        body = _check_note(note, report.errors)
        if body is not None:
            bodies.append((note, body))
    bodies.extend((note, _check_reserved(note, report.errors)) for note in vault.reserved)
    if vault.notes and all(note.path != ROOT_INDEX for note in vault.reserved):
        report.errors.append(f"{ROOT_INDEX}: missing root index")

    for note, body in sorted(bodies, key=lambda item: item[0].path):
        for link in wikilinks_in(body):
            report.errors.append(f"{note.path}: wikilink {link}")
        for target in sorted(linked_paths(body, note.path)):
            if (root / target).exists():
                continue
            message = f"{note.path}: broken link to {target}"
            if expected_unresolved is not None and str(target) not in expected_unresolved:
                report.errors.append(message)
            else:
                report.warnings.append(message)

    report.warnings.extend(f"outside bundle: {p}" for p in vault.excluded)
    return report
