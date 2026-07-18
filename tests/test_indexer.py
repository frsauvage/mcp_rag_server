"""Tests unitaires pour indexer.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from indexer import (
    EXCLUDED_FILENAMES,
    EXCLUDED_ROOT_DIRS,
    Indexer,
    IndexReport,
)

# ── IndexReport ───────────────────────────────────────────────────────────────

class TestIndexReport:
    def test_summary_contains_directory(self):
        r = IndexReport(directory="/proj", files_found=10, chunks_generated=50)
        s = r.summary()
        assert "10" in s
        assert "50" in s

    def test_summary_contains_counts(self):
        r = IndexReport(directory="/proj", files_found=10, files_indexed=8,
                        files_cached=2, chunks_generated=50, chunks_embedded=48)
        s = r.summary()
        assert "10" in s
        assert "8" in s
        assert "2" in s
        assert "50" in s
        assert "48" in s

    def test_summary_no_errors_no_error_section(self):
        r = IndexReport(directory="/proj")
        assert "Erreurs" not in r.summary()

    def test_summary_with_errors_shows_files(self):
        r = IndexReport(directory="/proj", failed_files=["a.py", "b.py"])
        s = r.summary()
        assert "a.py" in s
        assert "b.py" in s

    def test_summary_truncates_after_10_errors(self):
        files = [f"file_{i}.py" for i in range(15)]
        r = IndexReport(directory="/proj", failed_files=files)
        s = r.summary()
        assert "autres" in s

    def test_default_values(self):
        r = IndexReport(directory="/proj")
        assert r.files_found == 0
        assert r.chunks_generated == 0
        assert r.failed_files == []


# ── Indexer._scan_files ────────────────────────────────────────────────────────

class TestScanFiles:
    def _make_store(self):
        store = MagicMock()
        store.stats.return_value = {"total_chunks": 0, "total_files_indexed": 0}
        store.upsert_chunks.return_value = 0
        return store

    def test_finds_python_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1\n" * 10)
        (tmp_path / "utils.py").write_text("def f(): pass\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=False)
        names = {f.name for f in files}
        assert "main.py" in names
        assert "utils.py" in names

    def test_excludes_non_supported_extensions(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "config.yaml").write_text("key: value")
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=False)
        names = {f.name for f in files}
        assert "data.csv" not in names
        assert "config.yaml" not in names

    def test_excludes_filenames(self, tmp_path):
        for name in EXCLUDED_FILENAMES:
            (tmp_path / name).write_text("x = 1\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=False)
        names = {f.name for f in files}
        for excl in EXCLUDED_FILENAMES:
            assert excl not in names

    def test_excludes_root_dirs(self, tmp_path):
        for d in list(EXCLUDED_ROOT_DIRS)[:2]:
            subdir = tmp_path / d
            subdir.mkdir()
            (subdir / "code.py").write_text("x = 1\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=True)
        for f in files:
            assert f.relative_to(tmp_path).parts[0] not in EXCLUDED_ROOT_DIRS

    def test_excludes_excluded_dirs(self, tmp_path):
        # Use explicitly lowercase dir names guaranteed to be in EXCLUDED_DIRS
        for d in ["__pycache__", "node_modules"]:
            subdir = tmp_path / "src" / d
            subdir.mkdir(parents=True)
            (subdir / "code.py").write_text("x = 1\n" * 10)
        (tmp_path / "src" / "good.py").write_text("x = 1\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=True)
        names = {f.name for f in files}
        assert "code.py" not in names
        assert "good.py" in names

    def test_non_recursive_excludes_subdirs(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "nested.py").write_text("x = 1\n" * 10)
        (tmp_path / "root.py").write_text("x = 1\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=False)
        names = {f.name for f in files}
        assert "root.py" in names
        assert "nested.py" not in names

    def test_recursive_includes_subdirs(self, tmp_path):
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "nested.py").write_text("x = 1\n" * 10)
        indexer = Indexer(self._make_store())
        files = indexer._scan_files(tmp_path, recursive=True)
        names = {f.name for f in files}
        assert "nested.py" in names


# ── Indexer.index_directory ───────────────────────────────────────────────────

class TestIndexDirectory:
    def _make_store(self, upsert_return=5):
        store = MagicMock()
        store.stats.return_value = {"total_chunks": upsert_return, "total_files_indexed": 1}
        store.upsert_chunks.return_value = upsert_return
        store.clear = MagicMock()
        return store

    @pytest.mark.asyncio
    async def test_missing_directory_raises(self, tmp_path):
        store = self._make_store()
        indexer = Indexer(store)
        with pytest.raises(ValueError, match="introuvable"):
            await indexer.index_directory(str(tmp_path / "nope"))

    @pytest.mark.asyncio
    async def test_returns_index_report(self, tmp_path):
        (tmp_path / "f.py").write_text("x = 1\n" * 10)
        store = self._make_store()

        with patch("indexer.chunk_file", return_value=[MagicMock(file_path=str(tmp_path / "f.py"))]):
            report = await Indexer(store).index_directory(str(tmp_path))

        assert isinstance(report, IndexReport)

    @pytest.mark.asyncio
    async def test_force_reindex_calls_clear(self, tmp_path):
        store = self._make_store()
        with patch("indexer.chunk_file", return_value=[]):
            await Indexer(store).index_directory(str(tmp_path), force_reindex=True)
        store.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_force_reindex_skips_clear(self, tmp_path):
        store = self._make_store()
        with patch("indexer.chunk_file", return_value=[]):
            await Indexer(store).index_directory(str(tmp_path), force_reindex=False)
        store.clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_chunking_failure_recorded_in_report(self, tmp_path):
        (tmp_path / "bad.py").write_text("content")
        store = self._make_store(upsert_return=0)

        with patch("indexer.chunk_file", side_effect=RuntimeError("boom")):
            report = await Indexer(store).index_directory(str(tmp_path))

        assert len(report.failed_files) == 1
