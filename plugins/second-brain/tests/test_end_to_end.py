"""End-to-end: scan, plan, apply, validate through the CLI on a real git vault."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from okfmig.cli import main

STAMP = "2026-11-01T10:00:00+01:00"
TODAY = "2026-11-16"

BROKEN_YAML = "---\ntitle: [unclosed\n---\nBody\n"
FIXED_YAML = "---\ntitle: Fixed\n---\nBody\n"

NOTION = """---
notion-id: abc-123  # imported from Notion
Type: Meeting
created: 2026-01-02
tags: [meeting]
---
# Kickoff

Discussed [[Plain]] with the team.
"""

TABLE_FORMS = """# Links

- [[Plain]]
- [[Plain|alias]]
- [[Plain#Section]]
- [[#Local Heading]]
- [[Plain#^block1]]
- ![[pic.png]]
- ![[pic.png|300]]
- ![[Plain]]
- [already](Plain.md)

## Local Heading
"""

MIXED = """Ambiguous [[Dup]] and unresolved [[Missing Note]].
Dates [[2026-11-09]] and [[2026-11-09|2026-11-09 16:04]], daily [[2026-01-15]].
Old home [[Work/index|work home]], project log [[Projects/log]].
Inline `[[Plain]]` stays.

```
[[Plain]]
```
"""

CURATED_INDEX = """---
title: Concepts
---
# Concepts

Hand-written intro.

- [[Idea One]] - first idea
"""

SB_LOG = """---
title: Log
---
# Second Brain Log

- 2026-11-05: Added concepts
  - child detail
- 2026-11-07 Wrote [[Idea One]]

A loose remark.
"""

CLAUDE = "# Rules\n\nNo em dashes. [[Not a link]]\n"
NESTED = "[[wiki]] in a nested repo\n"

FILES: dict[str, str | bytes] = {
    ".obsidian/app.json": "{}\n",
    ".okf/types.yaml": 'default: note\nrules:\n  - folder: "second-brain/concepts/**"\n    type: concept\n',
    "CLAUDE.md": CLAUDE,
    "Plain.md": "# Plain Note\n\nJust text.\n\n## Section\n\nPara ^block1\n",
    "Empty.md": "",
    "Broken.md": BROKEN_YAML,
    "Links.md": TABLE_FORMS,
    "assets/pic.png": b"\x89PNG\r\n\x1a\n",
    "Notion/Meeting & Notes.md": NOTION,
    "Work/Mixed Stuff.md": MIXED,
    "Work/index.md": "# Work home\n\nJust prose about work.\n",
    "Work/Other.md": "Other work.\n",
    "A/Dup.md": "Dup A\n",
    "B/Sub/Dup.md": "Dup B\n",
    "Daily/2026-01-15.md": "Daily note.\n",
    "Projects/log.md": "Thoughts on projects, no dates.\n",
    "Projects/Plan.md": "Plan.\n",
    "second-brain/CLAUDE.md": CLAUDE,
    "second-brain/log.md": SB_LOG,
    "second-brain/concepts/index.md": CURATED_INDEX,
    "second-brain/concepts/Idea One.md": "One idea.\n",
    "second-brain/concepts/Idea Two.md": "Two idea.\n",
}


# -- helpers -------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def init_repo(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8")


def plan_json(root: Path) -> dict:
    return json.loads(read(root, ".okf-migrate/plan.json"))


@pytest.fixture
def vault(make_vault: Callable, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_AUTHOR_DATE", STAMP)
    monkeypatch.setenv("GIT_COMMITTER_DATE", STAMP)
    root = make_vault(FILES)
    nested = root / "Training/nb"
    nested.mkdir(parents=True)
    (nested / "notes.md").write_text(NESTED, encoding="utf-8")
    init_repo(nested)
    commit_all(nested, "nested")
    init_repo(root)
    commit_all(root, "initial")
    return root


def migrate(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The full first-run flow, asserting each step."""
    v = str(root)
    code, out = run(capsys, "scan", v)
    assert code == 0, out
    assert (root / ".okf-migrate/inventory.json").exists()

    code, out = run(capsys, "plan", v, "--today", TODAY)
    assert code == 0, out
    assert "Broken.md: invalid frontmatter" in out
    assert any(b.startswith("Broken.md") for b in plan_json(root)["blocking"])
    assert "plan.md" in out

    code, out = run(capsys, "apply", v)
    assert code == 1, out
    assert "blocking" in out.lower()

    (root / "Broken.md").write_text(FIXED_YAML, encoding="utf-8")
    commit_all(root, "fix yaml")

    code, out = run(capsys, "plan", v, "--today", TODAY)
    assert code == 0, out
    assert plan_json(root)["blocking"] == []

    code, out = run(capsys, "apply", v)
    assert code == 0, out
    assert "Work/work-index.md" in out

    code, out = run(capsys, "validate", v, "--plan", str(root / ".okf-migrate/plan.json"))
    assert code == 0, out


# -- tests ---------------------------------------------------------------------


def test_full_flow(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    migrate(vault, capsys)

    assert read(vault, "Empty.md") == f"---\ntype: note\ntitle: Empty\ntimestamp: '{STAMP}'\n---\n"

    assert read(vault, "Notion/Meeting & Notes.md") == (
        "---\n"
        "notion-id: abc-123  # imported from Notion\n"
        "Type: Meeting\n"
        "created: 2026-01-02\n"
        "tags: [meeting]\n"
        "type: note\n"
        "title: Kickoff\n"
        f"timestamp: '{STAMP}'\n"
        "---\n"
        "# Kickoff\n"
        "\n"
        "Discussed [Plain](../Plain.md) with the team.\n"
    )

    assert read(vault, "Links.md") == (
        "---\n"
        "type: note\n"
        "title: Links\n"
        f"timestamp: '{STAMP}'\n"
        "---\n"
        "# Links\n"
        "\n"
        "- [Plain](Plain.md)\n"
        "- [alias](Plain.md)\n"
        "- [Plain > Section](Plain.md#Section)\n"
        "- [Local Heading](#Local%20Heading)\n"
        "- [Plain](Plain.md#^block1)\n"
        "- ![pic.png](assets/pic.png)\n"
        "- ![pic.png|300](assets/pic.png)\n"
        "- ![Plain](Plain.md)\n"
        "- [already](Plain.md)\n"
        "\n"
        "## Local Heading\n"
    )

    assert read(vault, "Work/Mixed Stuff.md") == (
        "---\n"
        "type: note\n"
        "title: Mixed Stuff\n"
        f"timestamp: '{STAMP}'\n"
        "---\n"
        "Ambiguous [Dup](../A/Dup.md) and unresolved [Missing Note](../Missing%20Note.md).\n"
        "Dates 2026-11-09 and 2026-11-09 16:04, daily [2026-01-15](../Daily/2026-01-15.md).\n"
        "Old home [work home](work-index.md), project log [Projects/log](../Projects/projects-log.md).\n"
        "Inline `[[Plain]]` stays.\n"
        "\n"
        "```\n"
        "[[Plain]]\n"
        "```\n"
    )

    assert read(vault, "second-brain/concepts/index.md") == (
        "# Concepts\n"
        "\n"
        "Hand-written intro.\n"
        "\n"
        "- [Idea One](Idea%20One.md) - first idea\n"
        "\n"
        "# Unlisted\n"
        "\n"
        "* [Idea Two](Idea%20Two.md)\n"
    )

    assert read(vault, "second-brain/log.md") == (
        "# Second Brain Log\n"
        "\n"
        "## 2026-11-07\n"
        "\n"
        "* Wrote [Idea One](concepts/Idea%20One.md)\n"
        "\n"
        "## 2026-11-05\n"
        "\n"
        "* Added concepts\n"
        "  - child detail\n"
        "\n"
        "## Undated\n"
        "\n"
        "A loose remark.\n"
    )

    assert read(vault, "log.md") == (
        "# Update Log\n"
        "\n"
        "## 2026-11-16\n"
        "\n"
        "* **Init**: Migrated vault to OKF (15 notes typed, 16 links converted, 9 indexes created).\n"
    )

    assert not (vault / "Work/index.md").read_text(encoding="utf-8").startswith("# Work home")
    assert "* [Work home](work-index.md)" in read(vault, "Work/index.md")
    assert read(vault, "Work/work-index.md").endswith("# Work home\n\nJust prose about work.\n")
    assert read(vault, "Projects/projects-log.md").endswith("Thoughts on projects, no dates.\n")
    assert not (vault / "Projects/log.md").exists()
    assert read(vault, "index.md").startswith('---\nokf_version: "0.2"\n---\n\n')

    assert read(vault, "CLAUDE.md") == CLAUDE
    assert read(vault, "second-brain/CLAUDE.md") == CLAUDE
    assert read(vault, "Training/nb/notes.md") == NESTED
    assert not (vault / "Training/index.md").exists()
    assert git(vault, "status", "--porcelain", "--", ".obsidian") == ""


def test_rerun_flow(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    migrate(vault, capsys)
    commit_all(vault, "migrated")

    (vault / "Work/Fresh.md").write_text("Fresh [[Plain]].\n", encoding="utf-8")
    (vault / "Work/Other.md").unlink()
    (vault / "second-brain/concepts/Idea Three.md").write_text("Three.\n", encoding="utf-8")
    commit_all(vault, "new notes")

    v = str(vault)
    code, out = run(capsys, "plan", v, "--today", TODAY)
    assert code == 0, out
    code, out = run(capsys, "apply", v)
    assert code == 0, out
    code, out = run(capsys, "validate", v, "--plan", str(vault / ".okf-migrate/plan.json"))
    assert code == 0, out

    work_index = read(vault, "Work/index.md")
    assert "* [Fresh](Fresh.md)" in work_index
    assert "Other" not in work_index

    curated = read(vault, "second-brain/concepts/index.md")
    assert curated.count("# Unlisted") == 1
    assert curated.endswith("# Unlisted\n\n* [Idea Two](Idea%20Two.md)\n* [Idea Three](Idea%20Three.md)\n")

    log = read(vault, "log.md")
    assert log.count("## 2026-11-16") == 1
    assert "* **Update**: Brought 2 notes to OKF (1 links converted, 2 indexes updated)." in log

    commit_all(vault, "rerun")
    code, out = run(capsys, "plan", v, "--today", TODAY)
    assert code == 0, out
    assert plan_json(vault)["ops"] == []


def test_dirty_tree_refused(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (vault / "Plain.md").write_text("changed\n", encoding="utf-8")
    code, out = run(capsys, "scan", str(vault))
    assert code == 2
    assert "Plain.md" in out


def test_apply_refuses_dirty_tree(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (vault / "Broken.md").write_text(FIXED_YAML, encoding="utf-8")
    commit_all(vault, "fix yaml")
    assert run(capsys, "plan", str(vault), "--today", TODAY)[0] == 0
    (vault / "Plain.md").write_text("changed\n", encoding="utf-8")
    code, out = run(capsys, "apply", str(vault))
    assert code == 2
    assert "Plain.md" in out


def test_bad_types_yaml_exits_2(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (vault / ".okf/types.yaml").write_text("default: Not Kebab\n", encoding="utf-8")
    code, out = run(capsys, "plan", str(vault))
    assert code == 2
    assert "kebab" in out


def test_not_a_repo_exits_2(make_vault: Callable, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_vault({"a.md": "A\n"})
    code, out = run(capsys, "scan", str(root))
    assert code == 2
    assert "git" in out


def test_git_ignored_notes_are_left_alone(make_vault: Callable, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_vault({".gitignore": "private/\n", "a.md": "A\n", ".okf/types.yaml": "default: note\n"})
    init_repo(root)
    commit_all(root, "initial")
    (root / "private").mkdir()
    (root / "private" / "secret.md").write_text("Secret [[a]]\n", encoding="utf-8")
    v = str(root)
    assert run(capsys, "plan", v, "--today", TODAY)[0] == 0
    assert "private" in plan_json(root)["excluded"]
    code, out = run(capsys, "apply", v)
    assert code == 0, out
    assert read(root, "private/secret.md") == "Secret [[a]]\n"
    assert not (root / "private" / "index.md").exists()
    code, out = run(capsys, "validate", v)
    assert code == 0, out
    assert "outside bundle: private" in out


def test_dot_vault_path_titles_root_index_with_dir_name(
    make_vault: Callable, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_vault({"a.md": "A\n", ".okf/types.yaml": "default: note\n"})
    init_repo(root)
    commit_all(root, "initial")
    monkeypatch.chdir(root)
    code, out = run(capsys, "plan", ".", "--today", TODAY)
    assert code == 0, out
    root_index = next(op for op in plan_json(root)["ops"] if op["path"] == "index.md")
    assert f"\n# {root.name}\n" in root_index["new_content"]


def test_types_yaml_syntax_error_exits_2(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (vault / ".okf/types.yaml").write_text("rules:\n  - folder: [unclosed\n", encoding="utf-8")
    code, out = run(capsys, "plan", str(vault))
    assert code == 2
    assert "invalid type rules" in out and "types.yaml" in out


SUB_FILES = {
    "vault/.okf/types.yaml": "default: note\n",
    "vault/Plain.md": "# Plain\n\nSee [[Other]].\n",
    "vault/Other.md": "# Other\n",
    "outside.md": "# Outside the vault\n",
}


@pytest.fixture
def sub_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault that is the `vault/` subfolder of a bigger repo."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", STAMP)
    monkeypatch.setenv("GIT_COMMITTER_DATE", STAMP)
    repo = tmp_path / "repo"
    for rel, text in SUB_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "initial")
    return repo / "vault"


def test_vault_in_subfolder_of_repo(sub_vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = sub_vault.parent
    (repo / "outside.md").write_text("dirty outside the vault\n", encoding="utf-8")
    v = str(sub_vault)
    for argv in (("scan", v), ("plan", v, "--today", TODAY), ("apply", v), ("validate", v)):
        code, out = run(capsys, *argv)
        assert code == 0, (argv, out)
    assert "vault/.okf-migrate/" in read(repo, ".git/info/exclude").splitlines()
    assert read(sub_vault, "Plain.md") == (
        f"---\ntype: note\ntitle: Plain\ntimestamp: '{STAMP}'\n---\n# Plain\n\nSee [Other](Other.md).\n"
    )
    assert read(repo, "outside.md") == "dirty outside the vault\n"


def test_vault_in_subfolder_dirty_inside_refused(sub_vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (sub_vault / "Plain.md").write_text("changed\n", encoding="utf-8")
    code, out = run(capsys, "scan", str(sub_vault))
    assert code == 2
    assert "  Plain.md" in out
    assert "vault/Plain.md" not in out
