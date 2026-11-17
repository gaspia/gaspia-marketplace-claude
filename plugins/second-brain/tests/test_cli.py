import subprocess
from pathlib import Path

from okfmig.cli import main

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "okf_migrate.py"


def test_help_lists_subcommands():
    result = subprocess.run(["uv", "run", str(SCRIPT), "--help"], check=False, capture_output=True, text=True)
    assert result.returncode == 0
    for name in ("scan", "plan", "apply", "validate"):
        assert name in result.stdout


def test_unknown_vault_exits_2(capsys):
    assert main(["scan", "/nonexistent"]) == 2
    assert "/nonexistent" in capsys.readouterr().err


def test_scan_outside_git_repo_exits_2(make_vault):
    assert main(["scan", str(make_vault({"a.md": "x"}))]) == 2


def test_fixtures_write_str_and_bytes(make_vault):
    root = make_vault({"d/a.md": "héllo", "b.bin": b"\x00\x01"})
    assert (root / "d/a.md").read_text(encoding="utf-8") == "héllo"
    assert (root / "b.bin").read_bytes() == b"\x00\x01"


def test_git_vault_is_committed(git_vault):
    root = git_vault({"a.md": "x"})
    out = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=False, capture_output=True, text=True).stdout
    assert out == ""
    assert subprocess.run(
        ["git", "log", "--oneline"], cwd=root, check=False, capture_output=True, text=True
    ).stdout.strip()
