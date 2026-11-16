import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

Files = dict[str, str | bytes]


def _write(root: Path, files: Files) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def make_vault(tmp_path: Path) -> Callable[[Files], Path]:
    def factory(files: Files) -> Path:
        return _write(tmp_path / "vault", files)

    return factory


@pytest.fixture
def git_vault(make_vault: Callable[[Files], Path]) -> Callable[[Files], Path]:
    def factory(files: Files) -> Path:
        root = make_vault(files)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("config", "commit.gpgsign", "false")
        git("add", "-A")
        git("commit", "-q", "-m", "initial", "--allow-empty")
        return root

    return factory
