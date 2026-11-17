import subprocess
from pathlib import Path, PurePosixPath

from okfmig.gitutil import (
    commit_times_under,
    dirty_paths,
    dirty_under,
    ensure_info_exclude,
    ignored_paths,
    is_repo,
    last_commit_times,
    mtime_iso,
    repo_location,
)

WEIRD = "Eval&Community/Chat with Architect - João.md"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_clean_ignores_obsidian_and_okf(git_vault):
    root = git_vault({"a.md": "x", ".obsidian/workspace.json": "{}"})
    (root / ".obsidian" / "workspace.json").write_text("{1}")
    (root / ".okf").mkdir()
    (root / ".okf" / "types.yaml").write_text("t: 1")
    assert dirty_paths(root) == []
    (root / "a.md").write_text("changed")
    assert dirty_paths(root) == ["a.md"]


def test_dirty_paths_special_names(git_vault):
    root = git_vault({WEIRD: "x"})
    (root / WEIRD).write_text("y")
    (root / "new dir").mkdir()
    (root / "new dir" / "é & b.md").write_text("n")
    assert sorted(dirty_paths(root)) == sorted([WEIRD, "new dir/é & b.md"])


def test_last_commit_times(git_vault):
    root = git_vault({"a.md": "1", WEIRD: "1"})
    (root / "a.md").write_text("2")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "second")
    (root / "untracked.md").write_text("u")
    times = last_commit_times(root)
    expected = _git(root, "log", "-1", "--format=%cI", "--", "a.md").strip()
    assert times[PurePosixPath("a.md")] == expected
    assert PurePosixPath(WEIRD) in times
    assert PurePosixPath("untracked.md") not in times


def test_commit_times_under_is_relative_to_folder(git_vault):
    root = git_vault({"brain/concepts/a.md": "1", "other.md": "1"})
    expected = last_commit_times(root)[PurePosixPath("brain/concepts/a.md")]
    assert commit_times_under(root / "brain") == {PurePosixPath("concepts/a.md"): expected}
    assert commit_times_under(root) == last_commit_times(root)


def test_commit_times_under_without_commits_or_repo(tmp_path):
    assert commit_times_under(tmp_path) == {}
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.md").write_text("1")
    assert commit_times_under(tmp_path) == {}


def test_info_exclude_idempotent(git_vault):
    root = git_vault({"a.md": "1"})
    ensure_info_exclude(root)
    ensure_info_exclude(root)
    text = (root / ".git" / "info" / "exclude").read_text()
    assert text.splitlines().count(".okf-migrate/") == 1
    (root / ".okf-migrate").mkdir()
    (root / ".okf-migrate" / "x").write_text("1")
    assert ".okf-migrate" not in _git(root, "status", "--porcelain")


def test_not_a_repo(tmp_path):
    assert is_repo(tmp_path) is False


def test_subdir_of_repo_is_not_repo(git_vault):
    root = git_vault({"sub/a.md": "1"})
    assert is_repo(root) is True
    assert is_repo(root / "sub") is False


def test_mtime_iso_has_offset(tmp_path):
    f = tmp_path / "f.md"
    f.write_text("x")
    value = mtime_iso(f)
    assert len(value) == 25 and value[19] in "+-"


def test_ignored_paths_lists_untracked_ignored_dirs_and_files(git_vault):
    root = git_vault({".gitignore": "private/\n*.tmp.md\n", "a.md": "x", "sub/kept.md": "k"})
    (root / "private").mkdir()
    (root / "private" / "secret.md").write_text("s")
    (root / "sub" / "x.tmp.md").write_text("t")
    (root / "only").mkdir()
    (root / "only" / "y.tmp.md").write_text("t")
    # a directory holding only ignored files is reported once, without its contents
    assert ignored_paths(root) == [PurePosixPath(p) for p in ("only", "private", "sub/x.tmp.md")]


def test_repo_location_top_and_subfolder(git_vault):
    root = git_vault({"brain/concepts/a.md": "1"})
    assert repo_location(root) == (root.resolve(), PurePosixPath("."))
    assert repo_location(root / "brain/concepts") == (root.resolve(), PurePosixPath("brain/concepts"))


def test_repo_location_outside_git(tmp_path):
    assert repo_location(tmp_path) is None


def test_dirty_under_is_relative_to_folder(git_vault):
    root = git_vault({"brain/a.md": "1", "other.md": "1", "brain/.obsidian/x.json": "{}"})
    (root / "other.md").write_text("2")
    (root / "brain/.okf").mkdir()
    (root / "brain/.okf/types.yaml").write_text("t: 1")
    (root / "brain/.obsidian/x.json").write_text("{1}")
    assert dirty_under(root / "brain") == []
    (root / "brain/a.md").write_text("2")
    (root / "brain/new.md").write_text("n")
    assert sorted(dirty_under(root / "brain")) == ["a.md", "new.md"]
    # from the top, the brain's own .obsidian/ and .okf/ are ordinary content
    assert sorted(dirty_under(root)) == [
        "brain/.obsidian/x.json",
        "brain/.okf/types.yaml",
        "brain/a.md",
        "brain/new.md",
        "other.md",
    ]
