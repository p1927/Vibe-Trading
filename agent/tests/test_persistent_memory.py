"""Tests for PersistentMemory: file-based cross-session memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.persistent import (
    MAX_ENTRY_CHARS,
    MemoryEntry,
    PersistentMemory,
    _coerce_str,
    _sanitize_body,
    _tokenize,
    _truncate_body,
)


class TestCoerceStr:
    def test_passthrough_string(self) -> None:
        assert _coerce_str("hello") == "hello"

    def test_none_uses_default(self) -> None:
        assert _coerce_str(None, default="fallback") == "fallback"

    def test_list_joined_with_comma(self) -> None:
        # `description: [red]inject[/red]` would parse to a single-element list
        # because the frontmatter parser treats ``[...]`` as a list literal.
        assert _coerce_str(["red]inject[/red"]) == "red]inject[/red"
        assert _coerce_str(["a", "b"]) == "a, b"

    def test_bool_lowercased(self) -> None:
        assert _coerce_str(True) == "true"
        assert _coerce_str(False) == "false"


class TestScanEntriesCoercesFrontmatter:
    def test_bracketed_description_renders_as_string(self, tmp_path) -> None:
        # Regression: a description like ``[red]x[/red]`` parsed as a list used
        # to leak through MemoryEntry.description and crash any downstream
        # consumer that called string ops on it (e.g. rich.markup.escape).
        entry_path = tmp_path / "user_bracket-desc.md"
        entry_path.write_text(
            "---\nname: bracket-desc\ndescription: [red]inject[/red]\ntype: user\n---\n\nbody\n",
            encoding="utf-8",
        )
        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm.list_entries()
        assert len(entries) == 1
        assert isinstance(entries[0].description, str)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_ascii_words(self) -> None:
        tokens = _tokenize("hello world testing")
        assert "hello" in tokens
        assert "world" in tokens
        assert "testing" in tokens

    def test_single_char_words_excluded(self) -> None:
        tokens = _tokenize("I a")
        # Single ASCII chars carry no discriminating value, should be excluded
        assert len(tokens) == 0

    def test_two_char_words_included(self) -> None:
        # Regression: the fallback token-scan used a 3-char minimum while
        # search_index.py's FTS5 sanitizer used 2 chars, so short tokens like
        # ticker symbols ("GE") or country codes ("US") were retrievable via
        # FTS but silently invisible whenever VT_MEMORY_FTS_INDEX was off.
        tokens = _tokenize("GE US ok no")
        assert tokens == {"ge", "us", "ok", "no"}

    def test_cjk_characters(self) -> None:
        tokens = _tokenize("比特币价格分析")
        assert "比" in tokens
        assert "币" in tokens
        assert "价" in tokens

    def test_mixed(self) -> None:
        tokens = _tokenize("AAPL 苹果 stock analysis")
        assert "aapl" in tokens
        assert "苹" in tokens
        assert "stock" in tokens
        assert "analysis" in tokens

    def test_empty(self) -> None:
        assert _tokenize("") == set()

    def test_underscores_split(self) -> None:
        # snake_case titles must match natural-language queries.
        # Regression: previously _tokenize treated underscores as word chars,
        # so "mcp_wiring_test" became a single token and queries like
        # "mcp wiring" never matched.
        tokens = _tokenize("mcp_wiring_test")
        assert tokens == {"mcp", "wiring", "test"}

    def test_thai_characters(self) -> None:
        # Thai script (฀-๿) was not tokenized — recall on Thai
        # queries always returned the empty set. Char-level like CJK.
        tokens = _tokenize("นโยบายการเทรด")
        assert "น" in tokens
        assert "เ" in tokens
        assert "ท" in tokens

    def test_arabic_characters(self) -> None:
        tokens = _tokenize("التداول")
        assert "ا" in tokens
        assert "ل" in tokens

    def test_hebrew_characters(self) -> None:
        tokens = _tokenize("מסחר")
        assert "מ" in tokens
        assert "ס" in tokens

    def test_cyrillic_characters(self) -> None:
        tokens = _tokenize("торговля")
        assert "т" in tokens
        assert "о" in tokens


# ---------------------------------------------------------------------------
# PersistentMemory.add
# ---------------------------------------------------------------------------


class TestAdd:
    def test_creates_file_and_index(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("test-mem", "Some content", "project", description="Test desc")
        assert path.exists()
        assert "test-mem" in path.read_text(encoding="utf-8")
        index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        assert "test-mem" in index

    def test_slug_sanitization(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("My Fancy Skill!", "body", "user")
        assert "my_fancy_skill_" in path.name

    def test_frontmatter_structure(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("meta-test", "body here", "feedback", description="one line")
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "name: meta-test" in text
        assert "type: feedback" in text
        assert "description: one line" in text
        assert "body here" in text

    def test_multiple_adds(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("mem-a", "aaa", "project")
        pm.add("mem-b", "bbb", "user")
        pm.add("mem-c", "ccc", "reference")
        md_files = list(tmp_path.glob("*.md"))
        # 3 entries + MEMORY.md = 4
        assert len(md_files) == 4

    def test_overwrite_same_name(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("overwrite", "v1", "project")
        pm.add("overwrite", "v2", "project")
        # Should overwrite the same file
        path = tmp_path / "project_overwrite.md"
        assert "v2" in path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("title", ["นโยบาย", "التداول", "מסחר", "торговля"])
    def test_slug_preserves_non_latin_chars(self, tmp_path: Path, title: str) -> None:
        # Regression: non-Latin chars used to collapse to "_" in slug,
        # causing two distinct titles of equal length to collide.
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add(title, "body", "user")
        assert title in path.name

    def test_slug_distinguishes_two_thai_titles(self, tmp_path: Path) -> None:
        # Two different Thai titles must produce different files. Without the
        # fix both would collapse to "user________.md".
        pm = PersistentMemory(memory_dir=tmp_path)
        a = pm.add("นโยบาย", "rule a", "user")
        b = pm.add("กลยุทธ์", "rule b", "user")
        assert a != b
        assert "rule a" in a.read_text(encoding="utf-8")
        assert "rule b" in b.read_text(encoding="utf-8")

    def test_index_update_not_duplicate(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("dup-check", "v1", "project")
        pm.add("dup-check", "v2", "project")
        index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        assert index.count("[dup-check]") == 1

    def test_cjk_names_get_distinct_filenames(self, tmp_path: Path) -> None:
        # Regression: previously every non-ASCII char was replaced with `_`, so
        # any two CJK-only names of the same length collapsed to the same slug
        # (e.g. "上证指数" and "黄金价格" both → "____") and the second add
        # silently overwrote the first.
        pm = PersistentMemory(memory_dir=tmp_path)
        path1 = pm.add("上证指数", "A股大盘", "project", description="A股市场")
        path2 = pm.add("黄金价格", "黄金现货", "project", description="贵金属")
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()
        # Both bodies preserved on disk.
        assert "A股大盘" in path1.read_text(encoding="utf-8")
        assert "黄金现货" in path2.read_text(encoding="utf-8")
        # Index lists both.
        index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        assert "上证指数" in index
        assert "黄金价格" in index

    def test_cjk_name_is_findable_after_add(self, tmp_path: Path) -> None:
        # The frontmatter name still carries the original CJK title, so search
        # by CJK token still hits even though the filename slug is mangled.
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("人民币汇率", "USD/CNY 中间价", "project", description="汇率播报")
        results = pm.find_relevant("人民币")
        assert len(results) == 1
        assert results[0].title == "人民币汇率"

    def test_update_index_exact_title_match(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("Ref", "referencing main", "project", description="See details in [Main]")
        pm.add("Main", "main content", "project", description="Main memory entry")
        index_content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        lines = [line for line in index_content.splitlines() if line.strip()]
        assert len(lines) == 2
        assert any(line.startswith("- [Ref](") for line in lines)
        assert any(line.startswith("- [Main](") for line in lines)

    def test_hierarchy_routed_entry_stays_scannable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Regression: with VT_MEMORY_HIERARCHY enabled, add() used to hand the
        # bare slug to MemoryHierarchy.route_entry(), which treats its argument
        # as the leaf filename verbatim — the entry was written without the
        # .md extension and scan_all() (suffix == ".md" filter) could not see
        # it, so the memory vanished from list_entries()/find().
        monkeypatch.setenv("VT_MEMORY_HIERARCHY", "1")
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("routed-mem", "body text", "project", description="routed")
        assert path.suffix == ".md"
        assert path.parent.name == "project"
        entries = pm.list_entries()
        assert len(entries) == 1
        assert entries[0].title == "routed-mem"
        assert pm.find("routed-mem") is not None

    def test_recovered_orphan_and_new_write_agree_on_the_same_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The write path and the orphan recovery must name entries identically.

        ``recover_extensionless_entries`` renames an orphan by appending the
        missing suffix, so it produces ``<category>/<slug>.md``. If the write
        path is ever changed to a different convention, the same logical entry
        exists twice — once under the recovered name, once under the new one —
        and both show up in ``list_entries()``. This pins the agreement rather
        than the spelling: it asks the two code paths for the same entry and
        requires one file.
        """
        from src.memory.hierarchy import MemoryHierarchy

        monkeypatch.setenv("VT_MEMORY_HIERARCHY", "1")
        category = tmp_path / "project"
        category.mkdir()
        orphan = category / "shared-slug"
        orphan.write_text(
            "---\nname: shared-slug\ndescription: d\nmetadata:\n  type: project\n---\n\nold\n",
            encoding="utf-8",
        )
        recovered = MemoryHierarchy(tmp_path).recover_extensionless_entries()
        assert len(recovered) == 1

        written = PersistentMemory(memory_dir=tmp_path).add(
            "shared-slug", "new body", "project", description="d",
        )

        assert written == recovered[0]
        assert sorted(p.name for p in category.iterdir()) == ["shared-slug.md"]


# ---------------------------------------------------------------------------
# PersistentMemory.find_relevant
# ---------------------------------------------------------------------------


class TestArchiveEntryHierarchyCollision:
    def test_same_slug_different_categories_collide_in_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two entries sharing a title but living in different H-MEM category
        subdirs produce the same filename (route_entry() uses the bare slug
        with no category prefix), e.g. user/shared-title.md and
        project/shared-title.md. archive_entry() moves an entry to
        archive/<same filename>, with no uniqueness check, so archiving the
        second entry silently overwrites the first entry's archived content
        via entry.path.rename(dest) -- real data loss, not merely a naming
        quirk. This pins the actual current (buggy) behavior rather than
        assuming safety.
        """
        monkeypatch.setenv("VT_MEMORY_HIERARCHY", "1")
        pm = PersistentMemory(memory_dir=tmp_path)

        pm.add("shared-title", "content from user category", "user")
        pm.add("shared-title", "content from project category", "project")

        entries = {e.category: e for e in pm.list_entries()}
        assert set(entries) == {"user", "project"}

        user_entry = entries["user"]
        project_entry = entries["project"]
        # Same leaf filename, different category subdirectories.
        assert user_entry.path.name == project_entry.path.name == "shared-title.md"
        assert user_entry.path.parent != project_entry.path.parent

        assert pm.archive_entry(user_entry) is True
        assert pm.archive_entry(project_entry) is True

        archive_dir = tmp_path / "archive"
        archived_files = sorted(p.name for p in archive_dir.iterdir())
        # BUG: both entries collapsed into a single archived file -- the
        # "user" category's archived content is silently gone.
        assert archived_files == ["shared-title.md"]
        surviving = (archive_dir / "shared-title.md").read_text(encoding="utf-8")
        assert "content from project category" in surviving
        assert "content from user category" not in surviving


class TestAgentId:
    def test_agent_id_round_trips_through_add_and_scan(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("scoped-mem", "body", "project", agent_id="aa_test123")
        entries = pm.list_entries()
        entry = next(e for e in entries if e.title == "scoped-mem")
        assert entry.agent_id == "aa_test123"

    def test_agent_id_written_to_frontmatter(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("scoped-mem2", "body", "project", agent_id="aa_test456")
        assert "agent_id: aa_test456" in path.read_text(encoding="utf-8")

    def test_missing_agent_id_defaults_to_none(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("unscoped-mem", "body", "project")
        entries = pm.list_entries()
        entry = next(e for e in entries if e.title == "unscoped-mem")
        assert entry.agent_id is None

    def test_legacy_file_with_no_agent_id_field_parses_to_none(self, tmp_path: Path) -> None:
        # Simulates a pre-existing entry written before this field existed --
        # no `agent_id:` line in frontmatter at all.
        legacy = tmp_path / "project_legacy.md"
        legacy.write_text(
            "---\nname: legacy\ndescription: old entry\ntype: project\nid: abc123\n"
            "created_at: 2026-08-01T00:00:00\nupdated_at: 2026-08-01T00:00:00\n"
            "keywords: []\nquality_score: 0.5\naccess_count: 0\n"
            "last_accessed: 2026-08-01T00:00:00\nimportance: 0.5\nrelated_memories: []\n"
            "category: project\ncompression_level: raw\n---\n\nold body\n",
            encoding="utf-8",
        )
        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm.list_entries()
        entry = next(e for e in entries if e.title == "legacy")
        assert entry.agent_id is None

    def test_find_relevant_unaffected_by_agent_id(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("searchable-mem", "unique needle content", "project", agent_id="aa_x")
        results = pm.find_relevant("needle")
        assert any(e.title == "searchable-mem" for e in results)


class TestGitVersioning:
    def test_add_creates_repo_and_commit(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        from src.memory.versioning import ensure_repo

        ensure_repo(tmp_path)
        pm.add("versioned-mem", "body", "project")
        import subprocess

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        )
        assert log.returncode == 0
        # v0 checkpoint commit + this add's commit
        assert len(log.stdout.strip().splitlines()) >= 2

    def test_commit_is_best_effort_without_repo(self, tmp_path: Path) -> None:
        # No ensure_repo() call -- add() must still succeed with no git repo present.
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("no-repo-mem", "body", "project")
        assert path.exists()
        assert not (tmp_path / ".git").exists()

    def test_remove_commits_deletion(self, tmp_path: Path) -> None:
        from src.memory.versioning import ensure_repo

        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("to-remove", "body", "project")
        ensure_repo(tmp_path)
        pm.add("another", "body2", "project")
        assert pm.remove("another") is True
        import subprocess

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        )
        assert "remove: another" in log.stdout


class TestFindRelevant:
    def test_basic_search(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("btc-strategy", "Bitcoin mean reversion", "project", description="BTC trading strategy")
        pm.add("aapl-analysis", "Apple earnings report", "project", description="AAPL fundamental analysis")
        results = pm.find_relevant("Bitcoin trading")
        assert len(results) >= 1
        assert results[0].title == "btc-strategy"

    def test_cjk_search(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("a-share", "上证指数分析报告", "project", description="A股市场分析")
        results = pm.find_relevant("上证指数")
        assert len(results) >= 1

    def test_no_match(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("something", "unrelated content", "project")
        results = pm.find_relevant("xyznonexistent999")
        assert len(results) == 0

    def test_max_results(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        for i in range(10):
            pm.add(f"stock-{i}", f"stock analysis number {i}", "project", description=f"stock {i}")
        results = pm.find_relevant("stock analysis", max_results=3)
        assert len(results) == 3

    def test_max_results_with_semantic_links(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VT_MEMORY_LINKS", "true")
        pm = PersistentMemory(memory_dir=tmp_path)
        p1 = pm.add("stock-0", "stock analysis zero", "project", description="stock zero")
        p2 = pm.add("stock-1", "stock analysis one", "project", description="stock one")
        p3 = pm.add("stock-2", "stock analysis two", "project", description="stock two")
        assert p1 is not None and p2 is not None and p3 is not None
        from src.memory.semantic_links import SemanticLinker
        linker = SemanticLinker(tmp_path)
        linker.save_relations(p1, [(str(p3), 0.95)])
        results = pm.find_relevant("stock analysis", max_results=2)
        assert len(results) == 2

    def test_metadata_weighted_higher(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        # "bitcoin" in description (metadata) → weighted 2x
        pm.add("meta-match", "unrelated body text", "project", description="bitcoin trading strategy")
        # "bitcoin" only in body → weighted 1x
        pm.add("body-match", "bitcoin analysis deep dive", "project", description="some other topic")
        results = pm.find_relevant("bitcoin")
        assert len(results) == 2
        assert results[0].title == "meta-match"

    def test_empty_query(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("anything", "content", "project")
        results = pm.find_relevant("")
        assert results == []


# ---------------------------------------------------------------------------
# PersistentMemory.remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_existing(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("to-remove", "gone soon", "project")
        assert pm.remove("to-remove") is True
        # File gone
        assert not list(tmp_path.glob("*to_remove*"))
        # Index rebuilt without it
        index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
        assert "to-remove" not in index

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        assert pm.remove("ghost") is False

    def test_remove_then_find(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("ephemeral", "temporary data", "project", description="temp")
        pm.remove("ephemeral")
        results = pm.find_relevant("temporary")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# PersistentMemory.snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_loaded_at_init(self, tmp_path: Path) -> None:
        pm1 = PersistentMemory(memory_dir=tmp_path)
        pm1.add("snap-test", "content", "project", description="snapshot check")
        # New instance should load snapshot from MEMORY.md
        pm2 = PersistentMemory(memory_dir=tmp_path)
        assert "snap-test" in pm2.snapshot

    def test_snapshot_frozen(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        pm.add("after-init", "new content", "project")
        # Snapshot was frozen at init time (before add), so it should NOT contain "after-init"
        # unless the dir was empty at init (then snapshot is empty string)
        # In either case, snapshot should not update after add
        snap_before_check = pm.snapshot
        pm.add("another", "more content", "project")
        assert pm.snapshot == snap_before_check

    def test_empty_dir_snapshot(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        assert pm.snapshot == ""


class TestSanitizeBody:
    """Regression for #108 — strip C0/C1 control bytes from agent-supplied content."""

    def test_strips_ansi_escape(self) -> None:
        assert _sanitize_body("hello\x1b[31mred\x1b[0m world") == "hello[31mred[0m world"

    def test_strips_null_and_bell(self) -> None:
        assert _sanitize_body("a\x00b\x07c") == "abc"

    def test_preserves_tab_and_newline(self) -> None:
        assert _sanitize_body("line1\nline2\tindented") == "line1\nline2\tindented"

    def test_strips_c1_range(self) -> None:
        # U+0080 to U+009F are C1 controls (PAD, NEL, etc.)
        assert _sanitize_body("a\x80b\x9fc") == "abc"

    def test_empty_passthrough(self) -> None:
        assert _sanitize_body("") == ""


class TestTruncateBody:
    """Regression for #109 — enforce MAX_ENTRY_CHARS at write with visible marker."""

    def test_short_passthrough(self) -> None:
        assert _truncate_body("short") == "short"

    def test_at_limit_passthrough(self) -> None:
        text = "x" * MAX_ENTRY_CHARS
        assert _truncate_body(text) == text

    def test_over_limit_truncated_with_marker(self) -> None:
        text = "x" * (MAX_ENTRY_CHARS + 100)
        out = _truncate_body(text)
        # Total body length stays within MAX_ENTRY_CHARS so the marker survives
        # the read-side clip in _scan_entries.
        assert len(out) <= MAX_ENTRY_CHARS
        # Marker is at the tail; head still starts with content.
        assert out.startswith("x")
        assert out.endswith("chars]\n")
        assert "[truncated at" in out
        assert str(MAX_ENTRY_CHARS) in out

    def test_custom_limit(self) -> None:
        # Custom limit must be large enough to fit the marker plus some head.
        text = "abcdef" * 100  # 600 chars
        out = _truncate_body(text, limit=100)
        assert len(out) <= 100
        assert out.startswith("abc")
        assert "[truncated at 100 chars]" in out


class TestAddRejectsEmptyName:
    """Regression for #110 — reject empty / whitespace-only names."""

    def test_empty_raises(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        with pytest.raises(ValueError, match="empty or whitespace"):
            pm.add("", "body", "user")

    def test_whitespace_only_raises(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        with pytest.raises(ValueError, match="empty or whitespace"):
            pm.add("   ", "body", "user")

    def test_tab_only_raises(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        with pytest.raises(ValueError):
            pm.add("\t\n  ", "body", "user")


class TestAddHashSuffixForCollapsedSlug:
    """Regression for #110 — distinct emoji-only / punctuation-only names must
    produce distinct files via deterministic hash suffix."""

    def test_two_distinct_emoji_names_no_collision(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        p1 = pm.add("🚀", "rocket body", "reference")  # 🚀
        p2 = pm.add("🎯", "target body", "reference")  # 🎯
        assert p1 != p2
        assert "rocket body" in p1.read_text(encoding="utf-8")
        assert "target body" in p2.read_text(encoding="utf-8")

    def test_hash_is_deterministic(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        p1 = pm.add("🚀", "v1", "reference")
        p2 = pm.add("🚀", "v2", "reference")
        # Same name → same slug → overwrite (this is expected and desired
        # for the "edit memory" workflow).
        assert p1 == p2
        assert "v2" in p1.read_text(encoding="utf-8")

    def test_punctuation_only_name_gets_hash(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("???", "body", "user")
        # Slug ??? -> _ after sanitization; hash appended.
        # File name must not be just "user_.md".
        assert path.name != "user_.md"
        assert path.exists()


class TestAddSanitizesAndTruncates:
    """Regression for #108 + #109 wired into `PersistentMemory.add()`."""

    def test_add_strips_control_bytes_in_body(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("ctrl-test", "before\x1b[31mred\x1b[0mafter", "user")
        body_on_disk = path.read_text(encoding="utf-8")
        # ESC byte must be gone; surrounding text preserved.
        assert "\x1b" not in body_on_disk
        assert "before" in body_on_disk and "after" in body_on_disk
        assert "[31m" in body_on_disk  # the textual remainder is fine

    def test_add_truncates_long_body_with_marker(self, tmp_path: Path) -> None:
        pm = PersistentMemory(memory_dir=tmp_path)
        path = pm.add("long-content", "x" * (MAX_ENTRY_CHARS + 500), "reference")
        body_on_disk = path.read_text(encoding="utf-8").split("---\n\n", 1)[1]
        assert len(body_on_disk) <= MAX_ENTRY_CHARS + len("\n\n[truncated at  chars]\n") + 20
        assert "[truncated at" in body_on_disk
