from pathlib import PurePosixPath as P

import pytest
from okfmig.links import (
    DATE_RE,
    LinkChange,
    Resolver,
    encode_anchor,
    encode_path,
    linked_paths,
    outside_links,
    relative_href,
    rewrite_md_targets,
    rewrite_wikilinks,
    wikilinks_in,
)

FILES = [
    P("Work/Note.md"),
    P("Work/attachments/img.png"),
    P("Other/Note.md"),
    P("Notes/Embeddings.md"),
    P("Eval&Community/My Page.md"),
    P("2026-01-01.md"),
]
SOURCE = P("Work/Meeting.md")
ROOT = P(".")


def rewrite(body: str, files: list[P] | None = None, renames: dict | None = None, nff: P = ROOT):
    resolver = Resolver(files if files is not None else FILES, nff)
    return rewrite_wikilinks(body, SOURCE, resolver, renames or {})


@pytest.mark.parametrize(
    ("wiki", "expected"),
    [
        ("[[Notes/Embeddings]]", "[Notes/Embeddings](../Notes/Embeddings.md)"),
        ("[[Embeddings|emb]]", "[emb](../Notes/Embeddings.md)"),
        ("[[Embeddings#How it works]]", "[Embeddings > How it works](../Notes/Embeddings.md#How%20it%20works)"),
        ("[[#Intro]]", "[Intro](#Intro)"),
        ("[[Embeddings#^abc]]", "[Embeddings](../Notes/Embeddings.md#^abc)"),
        ("![[img.png]]", "![img.png](attachments/img.png)"),
        ("![[img.png|300]]", "![img.png|300](attachments/img.png)"),
        ("![[Embeddings]]", "![Embeddings](../Notes/Embeddings.md)"),
        ("[[My Page]]", "[My Page](../Eval&Community/My%20Page.md)"),
    ],
)
def test_table_forms(wiki: str, expected: str) -> None:
    out, changes = rewrite(f"see {wiki} here")
    assert out == f"see {expected} here"
    assert len(changes) == 1
    assert changes[0].original == wiki
    assert changes[0].replacement == expected
    assert changes[0].status == "resolved"


def test_ambiguous_prefers_closest() -> None:
    out, changes = rewrite("[[Note]]")
    assert out == "[Note](Note.md)"
    assert changes[0].status == "ambiguous"
    assert changes[0].target == P("Work/Note.md")
    assert set(changes[0].candidates) == {P("Work/Note.md"), P("Other/Note.md")}


def test_ambiguous_shortest_path_wins() -> None:
    files = [P("a/b/c/X.md"), P("X.md")]
    target, cands = Resolver(files, P(".")).resolve("X", P("a/b/c/S.md"))
    assert target == P("X.md")
    assert set(cands) == set(files)


def test_case_insensitive() -> None:
    out, changes = rewrite("[[embeddings]]")
    assert out == "[embeddings](../Notes/Embeddings.md)"
    assert changes[0].status == "resolved"


def test_path_suffix_match() -> None:
    resolver = Resolver([P("deep/Notes/Embeddings.md"), P("x/Embeddings.md")], P("."))
    assert resolver.resolve("notes/embeddings", P("a.md")) == (
        P("deep/Notes/Embeddings.md"),
        (P("deep/Notes/Embeddings.md"),),
    )
    assert resolver.resolve("tes/Embeddings", P("a.md")) == (None, ())


def test_unresolved_links_to_new_file_folder() -> None:
    out, changes = rewrite("[[Ghost Note]]")
    assert out == "[Ghost Note](../Ghost%20Note.md)"
    assert changes[0].status == "unresolved"
    assert changes[0].target == P("Ghost Note.md")


def test_unresolved_attachment_keeps_extension() -> None:
    out, changes = rewrite("![[missing.png]]", nff=P("Inbox"))
    assert out == "![missing.png](../Inbox/missing.png)"
    assert changes[0].status == "unresolved"


def test_dates() -> None:
    out, changes = rewrite("a [[2026-11-09]] b [[2026-11-09|2026-11-09 16:04]] c")
    assert out == "a 2026-11-09 b 2026-11-09 16:04 c"
    assert [c.status for c in changes] == ["date", "date"]
    out, changes = rewrite("[[2026-01-01]]")
    assert out == "[2026-01-01](../2026-01-01.md)"
    assert changes[0].status == "resolved"
    out, changes = rewrite("![[2026-11-09]]")
    assert out == "![2026-11-09](../2026-11-09.md)"
    assert changes[0].status == "unresolved"


def test_date_re() -> None:
    assert DATE_RE.match("2026-11-09 16:04")
    assert DATE_RE.match("2026-11-09T16:04:05")
    assert not DATE_RE.match("2026-11-09 notes")


def test_escaped_pipe_in_table() -> None:
    out, _ = rewrite("| [[Embeddings\\|emb]] |")
    assert out == "| [emb](../Notes/Embeddings.md) |"


def test_code_untouched() -> None:
    body = "`[[Note]]`\n```\n[[Note]]\n```\n"
    assert rewrite(body) == (body, [])


def test_existing_md_links_untouched() -> None:
    assert rewrite("[x](../Notes/Embeddings.md)") == ("[x](../Notes/Embeddings.md)", [])


def test_crlf_preserved() -> None:
    out, _ = rewrite("a [[Embeddings]]\r\nb\r\n")
    assert out == "a [Embeddings](../Notes/Embeddings.md)\r\nb\r\n"


def test_link_change_shape() -> None:
    _, changes = rewrite("[[Embeddings]]")
    assert changes == [
        LinkChange(
            "[[Embeddings]]",
            "[Embeddings](../Notes/Embeddings.md)",
            "resolved",
            P("Notes/Embeddings.md"),
            (P("Notes/Embeddings.md"),),
        )
    ]


def test_renames_applied() -> None:
    files = [*FILES, P("Work/index.md")]
    renames = {P("Work/index.md"): P("Work/work-index.md")}
    out, _ = rewrite("[[Work/index]]", files=files, renames=renames)
    assert out == "[Work/index](work-index.md)"
    assert rewrite_md_targets("[i](index.md)", SOURCE, SOURCE, renames) == ("[i](work-index.md)", 1)


def test_md_targets_keep_anchor_title_and_skip_external() -> None:
    renames = {P("Work/My index.md"): P("Work/work-index.md")}
    body = '[i](My%20index.md#Top "t") [h](https://x.y/index.md) [a](#x) [m](mailto:a@b) `[c](My%20index.md)`'
    out, n = rewrite_md_targets(body, SOURCE, SOURCE, renames)
    assert n == 1
    assert out == body.replace('(My%20index.md#Top "t")', '(work-index.md#Top "t")', 1)


def test_md_targets_rebased_when_source_moves() -> None:
    body = "![p](attachments/img.png) [n](../Notes/Embeddings.md) [u](Note.md)"
    out, n = rewrite_md_targets(body, P("Work/Meeting.md"), P("Work/Sub/Meeting.md"), {})
    assert out == "![p](../attachments/img.png) [n](../../Notes/Embeddings.md) [u](../Note.md)"
    assert n == 3


def test_md_targets_unchanged_when_nothing_moves() -> None:
    body = "[n](../Notes/Embeddings.md)"
    assert rewrite_md_targets(body, SOURCE, SOURCE, {}) == (body, 0)


def test_linked_paths() -> None:
    body = "[a](My%20Page.md#x) [b](https://x.y) [c](../Notes/Embeddings.md) ![i](img.png) [d](#y)"
    assert linked_paths(body, P("Eval&Community/index.md")) == {
        P("Eval&Community/My Page.md"),
        P("Notes/Embeddings.md"),
        P("Eval&Community/img.png"),
    }


def test_encoding_helpers() -> None:
    assert encode_path("a b(c)#d%e^f&g.md") == "a%20b%28c%29%23d%25e%5Ef&g.md"
    assert encode_anchor("How it works") == "How%20it%20works"
    assert relative_href(P("a/b.md"), P("c/My Page.md")) == "../c/My%20Page.md"
    assert relative_href(P("b.md"), P("c.md")) == "c.md"


def test_wikilinks_in() -> None:
    text = "related: [[A]], ![[b.png]] and `[[C]]`"
    assert wikilinks_in(text) == ["[[A]]", "![[b.png]]"]


def test_dotted_note_name_is_not_an_extension() -> None:
    out, changes = rewrite("[[Release v1.2]]")
    assert out == "[Release v1.2](../Release%20v1.2.md)"
    assert changes[0].status == "unresolved"
    out, changes = rewrite("[[release v1.2]]", files=[*FILES, P("Notes/Release v1.2.md")])
    assert out == "[release v1.2](../Notes/Release%20v1.2.md)"


def test_escaped_pipe_embed_in_table_keeps_escape() -> None:
    out, _ = rewrite("| ![[img.png\\|300]] |")
    assert out == "| ![img.png\\|300](attachments/img.png) |"
    out, _ = rewrite("![[img.png|300]]")
    assert out == "![img.png|300](attachments/img.png)"


def test_unresolved_leading_slash_target() -> None:
    out, changes = rewrite("[[/Ghost]]")
    assert out == "[/Ghost](../Ghost.md)"
    assert changes[0].status == "unresolved"
    assert changes[0].target == P("Ghost.md")


def test_date_with_heading_stays_link() -> None:
    out, changes = rewrite("[[2026-11-09#Morning]]")
    assert out == "[2026-11-09 > Morning](../2026-11-09.md#Morning)"
    assert changes[0].status == "unresolved"


def _rewrite_from(source: P, body: str, files: list[P], nff: P = ROOT):
    return rewrite_wikilinks(body, source, Resolver(files, nff), {})


def test_relative_wikilink_resolves_against_linking_folder() -> None:
    files = [P("B/y.md"), P("A/x.md"), P("A/B/y.md")]
    out, changes = _rewrite_from(P("A/x.md"), "[[../B/y]]", files)
    assert out == "[../B/y](../B/y.md)"
    assert changes[0].status == "resolved"
    assert changes[0].target == P("B/y.md")


def test_dot_slash_wikilink_is_case_insensitive_and_in_same_folder() -> None:
    files = [P("A/Sub/Note.md"), P("Sub/note.md")]
    out, changes = _rewrite_from(P("A/x.md"), "[[./sub/note|n]]", files)
    assert out == "[n](Sub/Note.md)"
    assert changes[0].status == "resolved"


def test_unresolved_relative_wikilink_points_to_normalized_vault_path() -> None:
    out, changes = _rewrite_from(P("A/x.md"), "[[../Missing]]", [P("A/x.md")], nff=P("Inbox"))
    assert out == "[../Missing](../Missing.md)"
    assert changes[0].status == "unresolved"
    assert changes[0].target == P("Missing.md")


def test_outside_links_reports_links_escaping_the_vault() -> None:
    body = "[in](../B/y.md) [out](../../z.md) [ext](https://x.y) `[c](../../q.md)`"
    assert outside_links(body, P("A/x.md")) == ["../../z.md"]
