import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from okfmig.mdtext import code_ranges, in_ranges, inline_tags
from okfmig.vault import discover

MakeVault = Callable[[dict[str, str | bytes]], Path]


def p(*paths: str) -> list[PurePosixPath]:
    return [PurePosixPath(x) for x in paths]


def test_discovers_notes_and_reserved(make_vault: MakeVault) -> None:
    vault = discover(make_vault({"a.md": "a", "x/index.md": "i", "x/log.md": "l", "x/b.md": "b"}))
    assert [n.path for n in vault.notes] == p("a.md", "x/b.md")
    assert [n.path for n in vault.reserved] == p("x/index.md", "x/log.md")
    assert vault.notes[0].text == "a"


def test_excludes_dot_dirs_nested_repo_and_claude_md(make_vault: MakeVault) -> None:
    root = make_vault(
        {
            ".obsidian/app.json": "{}",
            ".okf/types.yaml": "",
            "repo/.git/HEAD": "ref",
            "repo/r.md": "r",
            "CLAUDE.md": "c",
            "sub/AGENTS.md": "a",
            "n.md": "n",
        }
    )
    vault = discover(root)
    assert [n.path for n in vault.notes] == p("n.md")
    for expected in p("CLAUDE.md", "sub/AGENTS.md", "repo"):
        assert expected in vault.excluded
    assert PurePosixPath("repo/r.md") in vault.files
    assert PurePosixPath(".obsidian/app.json") not in vault.files


def test_extra_excludes_glob(make_vault: MakeVault) -> None:
    vault = discover(make_vault({"Templates/t.md": "t", "n.md": "n"}), extra_excludes=["Templates/**"])
    assert [n.path for n in vault.notes] == p("n.md")
    assert PurePosixPath("Templates/t.md") in vault.excluded


def test_undecodable_md_reported(make_vault: MakeVault) -> None:
    vault = discover(make_vault({"bad.md": b"\xff\xfe\x00bad", "ok.md": "ok"}))
    assert vault.undecodable == p("bad.md")
    assert [n.path for n in vault.notes] == p("ok.md")


def test_new_file_folder_setting(make_vault: MakeVault, tmp_path: Path) -> None:
    app = json.dumps({"newFileLocation": "folder", "newFileFolderPath": "Inbox"})
    assert discover(make_vault({".obsidian/app.json": app})).settings.new_file_folder == PurePosixPath("Inbox")
    other = tmp_path / "empty"
    other.mkdir()
    assert discover(other).settings.new_file_folder == PurePosixPath(".")


def test_code_ranges_and_inline_tags() -> None:
    body = "#real and `#code`\n```\n#fenced\n```\n# Heading\n#2026\n#a/b"
    assert inline_tags(body) == {"real", "a/b"}
    ranges = code_ranges(body)
    assert in_ranges(body.index("#code"), ranges)
    assert in_ranges(body.index("#fenced"), ranges)
    assert not in_ranges(body.index("#real"), ranges)


def test_note_text_roundtrips_crlf_and_bom(make_vault: MakeVault) -> None:
    vault = discover(make_vault({"crlf.md": b"a\r\nb\r\n", "bom.md": b"\xef\xbb\xbf---\r\n---\r\n"}))
    texts = {n.path.name: n.text for n in vault.notes}
    assert texts["crlf.md"] == "a\r\nb\r\n"
    assert texts["bom.md"].startswith("﻿")


def test_inline_code_does_not_span_blank_line() -> None:
    assert "real" in inline_tags("`unclosed\n\n#real\n\nlater `x`")
