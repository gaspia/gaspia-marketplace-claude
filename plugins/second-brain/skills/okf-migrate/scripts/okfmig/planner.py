"""Migration planner: compute every file change needed to bring a vault to OKF."""

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath
from typing import Literal

from okfmig import reserved as rsv
from okfmig.frontmatter import ensure_fields, first_h1, is_empty_value, parse
from okfmig.gitutil import mtime_iso
from okfmig.links import (
    LinkChange,
    Resolver,
    encode_path,
    linked_paths,
    relative_href,
    rewrite_md_targets,
    rewrite_wikilinks,
    wikilinks_in,
)
from okfmig.mdtext import inline_tags
from okfmig.typerules import TypeConfig, is_kebab, resolve_type
from okfmig.vault import Note, Vault

Action = Literal["create", "modify", "rename"]
ROOT = PurePosixPath(".")


@dataclass
class FileOp:
    path: str
    action: Action
    old_sha256: str | None
    new_content: str
    new_path: str | None = None


@dataclass
class Plan:
    ops: list[FileOp] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    dates_unlinked: list[dict] = field(default_factory=list)
    frontmatter_wikilinks: list[dict] = field(default_factory=list)
    nonkebab_types: list[dict] = field(default_factory=list)
    renames: dict[str, str] = field(default_factory=dict)
    stale_indexes: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    types_assigned: dict[str, dict] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def plan_to_json(plan: Plan) -> str:
    return json.dumps(dataclasses.asdict(plan), sort_keys=True, indent=2, ensure_ascii=False)


def plan_from_json(s: str) -> Plan:
    raw = json.loads(s)
    raw["ops"] = [FileOp(**op) for op in raw["ops"]]
    return Plan(**raw)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def kebab(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass
class _Info:
    """Index metadata of a note after the planned change."""

    title: str
    description: str | None


class _Planner:
    def __init__(self, vault: Vault, cfg: TypeConfig, timestamps: Mapping[PurePosixPath, str], today: date):
        self.vault = vault
        self.cfg = cfg
        self.timestamps = timestamps
        self.today = today
        self.plan = Plan(excluded=[str(p) for p in vault.excluded])
        self.renames: dict[PurePosixPath, PurePosixPath] = {}
        self.ops: list[FileOp] = []
        self.links_converted = 0
        self.notes_typed = 0
        self.indexes_created = 0
        self.indexes_updated = 0
        # final note path -> index metadata
        self.infos: dict[PurePosixPath, _Info] = {}
        self.indexes: dict[PurePosixPath, Note] = {}  # dir -> existing index file
        self.logs: list[Note] = []
        self.occupied: set[PurePosixPath] = set()  # reserved paths left in place that are not index/log
        self.resolver: Resolver | None = None
        self.on_disk: dict[str, list[PurePosixPath]] = {}  # lowercased path -> every file walked (incl. excluded)
        for f in vault.files:
            self.on_disk.setdefault(str(f).lower(), []).append(f)

    # -- steps ---------------------------------------------------------------

    def run(self) -> Plan:
        for path in self.vault.undecodable:
            self.plan.blocking.append(f"{path}: not valid UTF-8")
        notes = [(n, n.path) for n in self.vault.notes]
        notes.extend(self._reserved())
        self.resolver = Resolver(self.vault.files, self.vault.settings.new_file_folder)
        for note, new_path in sorted(notes, key=lambda item: item[1]):
            self._note(note, new_path)
        for log in self.logs:
            self._log(log)
        self._indexes()
        if self.ops:
            self._root_log()
        self.ops.sort(key=lambda op: (op.path, op.action != "rename"))
        self.plan.ops = self.ops
        self.plan.renames = {str(k): str(v) for k, v in sorted(self.renames.items())}
        self.plan.counts = self._counts()
        return self.plan

    def _reserved(self) -> list[tuple[Note, PurePosixPath]]:
        lower_files = {str(f).lower() for f in self.vault.files}
        as_notes: list[tuple[Note, PurePosixPath]] = []
        for note in self.vault.reserved:
            name = note.path.name
            kind = self.cfg.reserved.get(str(note.path)) or rsv.classify(name, note.text)  # type: ignore[arg-type]
            if kind == "note":
                folder = note.path.parent
                base = kebab(self.vault.root.name if folder == ROOT else folder.name) or "vault"
                new_path = folder / f"{base}-{name}"
                if str(new_path).lower() in lower_files:
                    self.plan.blocking.append(f"{note.path}: cannot rename to {new_path}, it already exists")
                    self.occupied.add(note.path)
                    continue
                self.renames[note.path] = new_path
                as_notes.append((note, new_path))
            elif f"{kind}.md" != name:
                self.plan.blocking.append(f"{note.path}: reserved kind {kind!r} does not match the file name")
                self.occupied.add(note.path)
            elif kind == "index":
                self.indexes[note.path.parent] = note
            else:
                self.logs.append(note)
        return as_notes

    def _note(self, note: Note, new_path: PurePosixPath) -> None:
        text, old_path = note.text, note.path
        parsed = parse(text)
        self.infos[new_path] = _Info(new_path.stem, None)
        if parsed.error is not None:
            self.plan.blocking.append(f"{old_path}: invalid frontmatter: {parsed.error}")
            return
        data = parsed.data or {}
        prefix = text[: len(text) - len(parsed.body)]
        for link in wikilinks_in(prefix):
            self.plan.frontmatter_wikilinks.append({"file": str(new_path), "link": link})
        type_, reason = resolve_type(self.cfg, new_path, data, inline_tags(parsed.body))
        if type_ is None:
            self.plan.unclassified.append(str(new_path))
            self.plan.blocking.append(f"{new_path}: unclassified")
            return
        if reason == "existing":
            if not is_kebab(type_):
                self.plan.nonkebab_types.append({"file": str(new_path), "type": type_})
        else:
            self.plan.types_assigned[str(new_path)] = {"type": type_, "reason": reason}

        fields = {"type": type_, "title": first_h1(parsed.body) or new_path.stem}
        if is_empty_value(data.get("timestamp")):
            fields["timestamp"] = self.timestamps.get(old_path) or mtime_iso(self.vault.root / old_path)
        try:
            with_fields = ensure_fields(text, fields)
        except ValueError as exc:
            self.plan.blocking.append(f"{old_path}: {exc}")
            return
        if with_fields != text:
            self.notes_typed += 1
        reparsed = parse(with_fields)
        self.infos[new_path] = _info(reparsed.data or {}, new_path.stem)
        head = with_fields[: len(with_fields) - len(reparsed.body)]
        body = self._convert(reparsed.body, new_path)
        body, _ = rewrite_md_targets(body, old_path, new_path, self.renames)
        self._emit(old_path, new_path, text, head + body)

    def _log(self, note: Note) -> None:
        new = rsv.regroup_log(note.text)  # regroup_log strips frontmatter first
        new = self._convert(new, note.path)
        new, _ = rewrite_md_targets(new, note.path, note.path, self.renames)
        self._emit(note.path, note.path, note.text, new)

    def _indexes(self) -> None:
        dirs: set[PurePosixPath] = set()
        for path in self.infos:
            dirs.update(path.parents)
        for d, existing in sorted(self.indexes.items()):
            if d not in dirs and rsv.is_generated(existing.text):
                self.plan.stale_indexes.append(str(existing.path))
        children: dict[PurePosixPath, list[PurePosixPath]] = {}
        for d in dirs:
            if d != ROOT:
                children.setdefault(d.parent, []).append(d)
        for d in sorted(dirs, key=lambda p: (-len(p.parts), str(p))):
            index_path = d / "index.md"
            if index_path in self.occupied:
                continue
            notes = [
                (p, rsv.Entry(info.title, relative_href(index_path, p), info.description))
                for p, info in self.infos.items()
                if p.parent == d
            ]
            subs = [
                (s / "index.md", rsv.Entry(s.name, encode_path(s.name) + "/index.md", None))
                for s in sorted(children.get(d, []))
            ]
            root = d == ROOT
            existing = self.indexes.get(d)
            if existing is not None and not rsv.is_generated(existing.text):
                converted = self._convert(existing.text, index_path)
                converted, _ = rewrite_md_targets(converted, index_path, index_path, self.renames)
                linked = linked_paths(converted, index_path)
                missing = sorted((e for p, e in [*notes, *subs] if p not in linked), key=lambda e: e.title.casefold())
                new = rsv.update_curated_index(converted, missing, root)
            else:
                title = self.vault.root.name if root else d.name
                new = rsv.render_generated_index(title, [e for _, e in notes], [e for _, e in subs], root)
            old = existing.text if existing is not None else None
            if self._emit(index_path, index_path, old, new):
                if old is None:
                    self.indexes_created += 1
                else:
                    self.indexes_updated += 1

    def _root_log(self) -> None:
        log_path = PurePosixPath("log.md")
        root_index = self.indexes.get(ROOT)
        initialized = False
        if root_index is not None:
            data = parse(root_index.text).data
            initialized = isinstance(data, dict) and "okf_version" in data
        if initialized:
            line = (
                f"* **Update**: Brought {self.notes_typed} notes to OKF ({self.links_converted} links converted, "
                f"{self.indexes_created + self.indexes_updated} indexes updated)."
            )
        else:
            line = (
                f"* **Init**: Migrated vault to OKF ({self.notes_typed} notes typed, {self.links_converted} links "
                f"converted, {self.indexes_created} indexes created)."
            )
        existing_op = next((op for op in self.ops if op.path == str(log_path) and op.action != "rename"), None)
        if existing_op is not None:
            existing_op.new_content = rsv.add_log_entry(existing_op.new_content, self.today, line)
            return
        current = next((n for n in self.logs if n.path == log_path), None)
        if current is None and log_path in self.occupied:
            self.plan.blocking.append(f"{log_path}: cannot add the migration log entry, file is not a log")
            return
        old = current.text if current is not None else None
        self._emit(log_path, log_path, old, rsv.add_log_entry(old, self.today, line))

    # -- helpers -------------------------------------------------------------

    def _convert(self, body: str, source: PurePosixPath) -> str:
        assert self.resolver is not None
        new, changes = rewrite_wikilinks(body, source, self.resolver, self.renames)
        for change in changes:
            self._record(source, change)
        return new

    def _record(self, source: PurePosixPath, change: LinkChange) -> None:
        item = {"file": str(source), "before": change.original, "after": change.replacement}
        if change.status == "date":
            self.plan.dates_unlinked.append(item)
            return
        self.links_converted += 1
        if change.status == "ambiguous":
            self.plan.ambiguous.append({**item, "candidates": [str(c) for c in change.candidates]})
        elif change.status == "unresolved":
            self.plan.unresolved.append(item)

    def _emit(self, old_path: PurePosixPath, new_path: PurePosixPath, old: str | None, new: str) -> bool:
        if old is None:
            clash = [p for p in self.on_disk.get(str(new_path).lower(), []) if p not in self.renames]
            if clash:
                self.plan.blocking.append(f"{new_path}: conflicts with existing {clash[0]} (case-insensitive)")
                return False
            self.ops.append(FileOp(str(new_path), "create", None, new))
            return True
        if old_path != new_path:
            self.ops.append(FileOp(str(old_path), "rename", sha256(old), new, str(new_path)))
            return True
        if new != old:
            self.ops.append(FileOp(str(old_path), "modify", sha256(old), new))
            return True
        return False

    def _counts(self) -> dict[str, int]:
        return {
            "notes_typed": self.notes_typed,
            "links_converted": self.links_converted,
            "indexes_created": self.indexes_created,
            "indexes_updated": self.indexes_updated,
            "dates_unlinked": len(self.plan.dates_unlinked),
        }


def _info(data: dict, stem: str) -> _Info:
    title = data.get("title")
    description = data.get("description")
    return _Info(
        title.strip() if isinstance(title, str) and title.strip() else stem,
        description.strip() if isinstance(description, str) and description.strip() else None,
    )


def build_plan(vault: Vault, cfg: TypeConfig, timestamps: Mapping[PurePosixPath, str], today: date) -> Plan:
    return _Planner(vault, cfg, timestamps, today).run()


from okfmig.report import build_inventory, render_inventory_summary, render_plan_md  # noqa: E402

__all__ = [
    "FileOp",
    "Plan",
    "build_inventory",
    "build_plan",
    "plan_from_json",
    "plan_to_json",
    "render_inventory_summary",
    "render_plan_md",
]
