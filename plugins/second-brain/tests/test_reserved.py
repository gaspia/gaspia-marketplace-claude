from datetime import date

from okfmig.reserved import (
    GENERATED_MARKER,
    Entry,
    add_log_entry,
    classify,
    is_generated,
    regroup_log,
    render_entry,
    render_generated_index,
    update_curated_index,
)

CONCEPTS_INDEX = """---
type: index
title: Concepts Index
description: Map of all concepts in the vault.
timestamp: 2026-11-05
---

# Concepts

- [Embeddings](embeddings.md): Numeric vector representations of data where closeness reflects similarity in meaning.
"""

REAL_LOG = """---
type: log
title: Log
description: Chronological capture and changes.
---

# Log

- 2026-11-05: Added concept [Embeddings](concepts/embeddings.md). Created `concepts/index.md` and `log.md`.
"""

EMBEDDINGS_LINE = (
    "- [Embeddings](embeddings.md): Numeric vector representations of data "
    "where closeness reflects similarity in meaning."
)


def test_classify() -> None:
    assert classify("index.md", CONCEPTS_INDEX) == "index"
    assert classify("index.md", "# Notes on indexes\nSome prose.") == "note"
    assert classify("index.md", "# X\n\n* [[Some Note]]\n") == "index"
    assert classify("index.md", f"# X\n\n{GENERATED_MARKER}\n") == "index"
    assert classify("log.md", REAL_LOG) == "log"
    assert classify("log.md", "# Logging\ntext") == "note"
    assert classify("log.md", "# L\n\n## 2026-11-05\n\n* x\n") == "log"


def test_render_entry() -> None:
    assert render_entry(Entry("A", "a.md", "D")) == "* [A](a.md) - D"
    assert render_entry(Entry("A", "a.md", None)) == "* [A](a.md)"


def test_generated_index_exact() -> None:
    out = render_generated_index(
        "Work",
        [Entry("b", "b.md", None), Entry("A", "A%20x.md", "Desc")],
        [Entry("Sub", "Sub/index.md", None)],
        root=False,
    )
    assert out == (
        "# Work\n\n* [A](A%20x.md) - Desc\n* [b](b.md)\n\n# Subfolders\n\n* [Sub](Sub/index.md)\n\n"
        "<!-- okf-migrate:generated -->\n"
    )
    assert is_generated(out)
    assert not is_generated(CONCEPTS_INDEX)


def test_root_generated_index_frontmatter() -> None:
    out = render_generated_index("Vault", [Entry("a", "a.md", None)], [], root=True)
    assert out.startswith('---\nokf_version: "0.2"\n---\n\n# Vault\n\n')
    assert "# Subfolders" not in out


def test_curated_index_keeps_lines_and_adds_unlisted() -> None:
    out = update_curated_index(CONCEPTS_INDEX, [Entry("Vector DB", "vector-db.md", None)], root=False)
    assert not out.startswith("---")
    assert "# Concepts\n" in out
    assert EMBEDDINGS_LINE + "\n" in out
    assert out.endswith("# Unlisted\n\n* [Vector DB](vector-db.md)\n")
    out2 = update_curated_index(out, [Entry("Other", "other.md", "d")], root=False)
    assert out2.count("# Unlisted") == 1
    assert out2.endswith("* [Vector DB](vector-db.md)\n* [Other](other.md) - d\n")
    assert update_curated_index(out2, [], root=False) == out2


def test_curated_index_root_frontmatter_idempotent() -> None:
    out = update_curated_index(CONCEPTS_INDEX, [], root=True)
    assert out.startswith('---\nokf_version: "0.2"\n---\n\n# Concepts\n')
    assert "type: index" not in out
    assert update_curated_index(out, [], root=True) == out


def test_curated_index_unlisted_mid_file_and_crlf() -> None:
    text = "# A\r\n\r\n- [x](x.md)\r\n\r\n# Unlisted\r\n\r\n* [y](y.md)\r\n\r\n# Tail\r\n\r\n- t\r\n"
    out = update_curated_index(text, [Entry("z", "z.md", None)], root=False)
    expected = "# A\r\n\r\n- [x](x.md)\r\n\r\n# Unlisted\r\n\r\n* [y](y.md)\r\n* [z](z.md)\r\n"
    assert out == expected + "\r\n# Tail\r\n\r\n- t\r\n"
    new = update_curated_index("# A\r\n\r\n- [x](x.md)\r\n", [Entry("z", "z.md", None)], root=False)
    assert new == "# A\r\n\r\n- [x](x.md)\r\n\r\n# Unlisted\r\n\r\n* [z](z.md)\r\n"


def test_regroup_real_log() -> None:
    assert regroup_log(REAL_LOG) == (
        "# Log\n\n## 2026-11-05\n\n* Added concept [Embeddings](concepts/embeddings.md). "
        "Created `concepts/index.md` and `log.md`.\n"
    )


def test_regroup_mixed() -> None:
    text = (
        "# Log\n\n"
        "- 2026-11-01: first\n"
        "  child of first\n"
        "* 2026-11-05 second\n"
        "- undated item\n\n"
        "Some prose paragraph.\n\n"
        "## 2026-11-05\n\n"
        "* existing\n"
    )
    out = regroup_log(text)
    assert out == (
        "# Log\n\n"
        "## 2026-11-05\n\n* second\n* existing\n\n"
        "## 2026-11-01\n\n* first\n  child of first\n\n"
        "## Undated\n\n- undated item\n\nSome prose paragraph.\n"
    )
    assert regroup_log(out) == out


def test_regroup_crlf_idempotent() -> None:
    out = regroup_log(REAL_LOG.replace("\n", "\r\n"))
    assert out == (
        "# Log\r\n\r\n## 2026-11-05\r\n\r\n* Added concept [Embeddings](concepts/embeddings.md). "
        "Created `concepts/index.md` and `log.md`.\r\n"
    )
    assert regroup_log(out) == out


def test_add_log_entry() -> None:
    today = date(2026, 11, 16)
    assert add_log_entry(None, today, "* x") == "# Update Log\n\n## 2026-11-16\n\n* x\n"
    existing = "# Log\n\n## 2026-11-05\n\n* old\n"
    out = add_log_entry(existing, today, "* a")
    assert out == "# Log\n\n## 2026-11-16\n\n* a\n\n## 2026-11-05\n\n* old\n"
    out2 = add_log_entry(out, today, "* b")
    assert out2 == "# Log\n\n## 2026-11-16\n\n* a\n* b\n\n## 2026-11-05\n\n* old\n"
    assert add_log_entry("# Log\n", today, "* a") == "# Log\n\n## 2026-11-16\n\n* a\n"
