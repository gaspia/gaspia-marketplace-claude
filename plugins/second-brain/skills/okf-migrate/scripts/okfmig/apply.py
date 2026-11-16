"""Apply a migration plan: verify every planned file is unchanged, then write atomically."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from okfmig.planner import FileOp, Plan


class BlockedPlanError(Exception):
    """The plan still has blocking items."""


class StaleFileError(Exception):
    """Files changed on disk since the plan was made."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stale(root: Path, plan: Plan) -> list[str]:
    moved_away = {op.path for op in plan.ops if op.action == "rename"}
    stale: list[str] = []
    for op in plan.ops:
        path = root / op.path
        if op.old_sha256 is None:
            if (path.exists() or path.is_symlink()) and op.path not in moved_away:
                stale.append(f"{op.path} (exists, plan expected to create it)")
        elif not path.is_file():
            stale.append(f"{op.path} (missing)")
        elif _sha256(path) != op.old_sha256:
            stale.append(f"{op.path} (changed since plan)")
        if op.action == "rename" and op.new_path is not None and (root / op.new_path).exists():
            stale.append(f"{op.new_path} (exists, plan expected to rename {op.path} to it)")
    return stale


def _new_file_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _write_atomic(path: Path, content: str, mode_from: Path | None) -> None:
    """Write via a temp file in the same dir; keep `mode_from`'s permissions, else honor the umask."""
    mode = stat.S_IMODE(mode_from.stat().st_mode) if mode_from is not None else _new_file_mode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _describe(op: FileOp) -> str:
    if op.action == "rename":
        return f"rename {op.path} -> {op.new_path}"
    return f"{op.action} {op.path}"


def apply_plan(root: Path, plan: Plan) -> list[str]:
    """Write every op in plan order; returns one line per change. Nothing is written on refusal."""
    if plan.blocking:
        raise BlockedPlanError("plan has blocking items:\n" + "\n".join(f"  {b}" for b in plan.blocking))
    stale = _stale(root, plan)
    if stale:
        raise StaleFileError("files changed since the plan was made:\n" + "\n".join(f"  {s}" for s in stale))
    changed: list[str] = []
    for op in plan.ops:
        if op.action == "rename":
            assert op.new_path is not None
            _write_atomic(root / op.new_path, op.new_content, mode_from=root / op.path)
            (root / op.path).unlink()
        else:
            target = root / op.path
            _write_atomic(target, op.new_content, mode_from=target if target.exists() else None)
        changed.append(_describe(op))
    return changed
