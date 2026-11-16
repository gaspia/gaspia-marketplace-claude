"""Human-readable reports: scan inventory and plan.md. Re-exported from okfmig.planner."""

from collections import Counter
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from okfmig.frontmatter import is_empty_value, parse
from okfmig.links import Resolver, rewrite_wikilinks
from okfmig.mdtext import inline_tags
from okfmig.reserved import classify
from okfmig.typerules import TypeConfig
from okfmig.vault import Vault

if TYPE_CHECKING:
    from okfmig.planner import Plan

_TOP = 20


def _hist(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _frontmatter_tags(data: dict) -> list[str]:
    raw = data.get("tags")
    items = raw if isinstance(raw, list) else str(raw).replace(",", " ").split() if isinstance(raw, str) else []
    return [str(t).strip().lstrip("#").casefold() for t in items if str(t).strip().lstrip("#")]


def build_inventory(vault: Vault, cfg: TypeConfig) -> dict:
    resolver = Resolver(vault.files, vault.settings.new_file_folder)
    types: Counter[str] = Counter()
    top: Counter[str] = Counter()
    second: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    links = {"wikilinks": 0, "embeds": 0, "dates": 0}
    with_fm = 0
    parse_errors: list[dict] = []
    notes: list[dict[str, Any]] = []

    for note in vault.notes:
        parsed = parse(note.text)
        data = parsed.data or {}
        with_fm += parsed.has_block
        if parsed.error is not None:
            parse_errors.append({"path": str(note.path), "error": parsed.error})
        if not is_empty_value(data.get("type")):
            types[str(data["type"])] += 1
        parts = note.path.parent.parts
        top[parts[0] if parts else "."] += 1
        if len(parts) >= 2:  # noqa: PLR2004
            second["/".join(parts[:2])] += 1
        body_tags = inline_tags(parsed.body)
        note_tags = sorted(set(_frontmatter_tags(data)) | body_tags)
        tags.update(note_tags)
        keys.update(str(k) for k in data)
        _, changes = rewrite_wikilinks(parsed.body, note.path, resolver, {})
        note_links = []
        for change in changes:
            statuses[change.status] += 1
            if change.status == "date":
                links["dates"] += 1
            elif change.original.startswith("!"):
                links["embeds"] += 1
            else:
                links["wikilinks"] += 1
            note_links.append({"link": change.original, "status": change.status})
        notes.append(
            {
                "path": str(note.path),
                "has_frontmatter": parsed.has_block,
                "frontmatter_keys": [str(k) for k in data],
                "error": parsed.error,
                "type": None if is_empty_value(data.get("type")) else str(data["type"]),
                "tags": note_tags,
                "links": note_links,
            }
        )

    reserved = [
        {
            "path": str(n.path),
            "classify": classify(n.path.name, n.text),  # type: ignore[arg-type]
            "override": cfg.reserved.get(str(n.path)),
        }
        for n in vault.reserved
    ]
    return {
        "notes": len(vault.notes),
        "with_frontmatter": with_fm,
        "without_frontmatter": len(vault.notes) - with_fm,
        "parse_errors": parse_errors,
        "undecodable": [str(p) for p in vault.undecodable],
        "types": _hist(types),
        "folders_top": _hist(top),
        "folders_second": _hist(second),
        "tags": _hist(tags),
        "frontmatter_keys": _hist(keys),
        "links": {**links, "by_status": _hist(statuses)},
        "reserved": reserved,
        "excluded": [str(p) for p in vault.excluded],
        "note_details": notes,
    }


def _hist_lines(title: str, hist: dict[str, int]) -> list[str]:
    lines = [f"{title} ({len(hist)}):"]
    items = list(hist.items())
    lines += [f"  {name}: {count}" for name, count in items[:_TOP]]
    if len(items) > _TOP:
        lines.append(f"  ... and {len(items) - _TOP} more")
    return lines


def render_inventory_summary(inv: dict) -> str:
    links = inv["links"]
    lines = [
        f"Notes: {inv['notes']}",
        f"With frontmatter: {inv['with_frontmatter']}",
        f"Without frontmatter: {inv['without_frontmatter']}",
        f"Frontmatter parse errors: {len(inv['parse_errors'])}",
    ]
    lines += [f"  {e['path']}: {e['error']}" for e in inv["parse_errors"]]
    lines.append(f"Undecodable files: {len(inv['undecodable'])}")
    lines += [f"  {p}" for p in inv["undecodable"]]
    lines += [
        f"Wikilinks: {links['wikilinks']}, embeds: {links['embeds']}, date links: {links['dates']}",
        "Link resolution: " + (", ".join(f"{k} {v}" for k, v in links["by_status"].items()) or "none"),
    ]
    lines += _hist_lines("Existing types", inv["types"])
    lines += _hist_lines("Top-level folders", inv["folders_top"])
    lines += _hist_lines("Second-level folders", inv["folders_second"])
    lines += _hist_lines("Tags", inv["tags"])
    lines += _hist_lines("Frontmatter keys", inv["frontmatter_keys"])
    lines.append(f"Reserved files ({len(inv['reserved'])}):")
    for r in inv["reserved"]:
        override = f" (override: {r['override']})" if r["override"] else ""
        lines.append(f"  {r['path']}: {r['classify']}{override}")
    lines.append(f"Excluded ({len(inv['excluded'])}):")
    lines += [f"  {p}" for p in inv["excluded"]]
    return "\n".join(lines) + "\n"


def _section(title: str, items: list[str]) -> list[str]:
    return [f"## {title}", "", *(items or ["None."]), ""]


def _link_items(items: list[dict]) -> list[str]:
    out = []
    for item in items:
        line = f"- {item['file']}: {item['before']} -> {item['after']}"
        if item.get("candidates"):
            line += f" (candidates: {', '.join(item['candidates'])})"
        out.append(line)
    return out


def render_plan_md(plan: Plan) -> str:
    c = plan.counts
    summary = [
        f"- Files changed: {len(plan.ops)}",
        f"- Notes typed: {c.get('notes_typed', 0)}",
        f"- Links converted: {c.get('links_converted', 0)}",
        f"- Dates unlinked: {c.get('dates_unlinked', 0)}",
        f"- Indexes created: {c.get('indexes_created', 0)}",
        f"- Indexes updated: {c.get('indexes_updated', 0)}",
        f"- Renames: {len(plan.renames)}",
        f"- Blocking issues: {len(plan.blocking)}",
        f"- Unclassified notes: {len(plan.unclassified)}",
        f"- Ambiguous links: {len(plan.ambiguous)}",
        f"- Unresolved links: {len(plan.unresolved)}",
    ]
    by_type: dict[str, list[str]] = {}
    for path, info in sorted(plan.types_assigned.items()):
        by_type.setdefault(info["type"], []).append(f"- {path} ({info['reason']})")
    types: list[str] = []
    for type_, items in sorted(by_type.items()):
        types += [f"### {type_} ({len(items)})", "", *items, ""]
    if types:
        types.pop()
    index_ops = [op for op in plan.ops if PurePosixPath(op.path).name == "index.md" and op.action != "rename"]
    files = [
        f"- rename {op.path} -> {op.new_path}" if op.action == "rename" else f"- {op.action} {op.path}"
        for op in plan.ops
    ]
    lines = ["# OKF migration plan", ""]
    lines += _section("Summary", summary)
    lines += _section("Blocking", [f"- {b}" for b in plan.blocking])
    lines += _section("Unclassified", [f"- {p}" for p in plan.unclassified])
    lines += _section("Renames", [f"- {old} -> {new}" for old, new in plan.renames.items()])
    lines += _section("Types assigned", types)
    lines += _section("Ambiguous links", _link_items(plan.ambiguous))
    lines += _section("Unresolved links", _link_items(plan.unresolved))
    lines += _section("Dates unlinked", _link_items(plan.dates_unlinked))
    lines += _section(
        "Frontmatter wikilinks (not changed)", [f"- {i['file']}: {i['link']}" for i in plan.frontmatter_wikilinks]
    )
    lines += _section(
        "Non-kebab existing types (informational)", [f"- {i['file']}: {i['type']}" for i in plan.nonkebab_types]
    )
    lines += _section("Indexes", [f"- {op.action} {op.path}" for op in index_ops])
    lines += _section("Excluded (outside bundle)", [f"- {p}" for p in plan.excluded])
    lines += _section("Files changed", files)
    return "\n".join(lines).rstrip("\n") + "\n"
