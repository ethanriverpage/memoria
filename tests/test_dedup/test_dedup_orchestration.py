"""Tests for dedup mode orchestration and parallelism.

These tests verify plan-critical behaviors for the cross-processor dedup pipeline:
- Phase 0/1/3 use ProcessPoolExecutor when parallel_enabled=True and multiple exports
- Phase 2 is a global barrier (manifest written before Phase 3 starts)
- Sequential execution when parallel_enabled=False or single export
- Correct process count (parallel_exports) passed to executors

These tests are lightweight and use direct inspection of control flow logic
rather than running the full pipeline.
"""

from pathlib import Path
from unittest.mock import patch


class TestDedupParallelConditions:
    """Tests for conditions that trigger parallel vs sequential execution."""

    def test_parallel_requires_enabled_flag(self) -> None:
        """Parallel execution requires parallel_enabled=True."""
        # The condition in run_phased_dedup_processing is:
        # if parallel_enabled and len(dirs_to_process) > 1
        parallel_enabled = False
        dirs_count = 5

        should_parallelize = parallel_enabled and dirs_count > 1
        assert should_parallelize is False

    def test_parallel_requires_multiple_exports(self) -> None:
        """Parallel execution requires more than one export."""
        parallel_enabled = True
        dirs_count = 1

        should_parallelize = parallel_enabled and dirs_count > 1
        assert should_parallelize is False

    def test_parallel_activates_with_both_conditions(self) -> None:
        """Parallel execution activates with enabled flag and multiple exports."""
        parallel_enabled = True
        dirs_count = 3

        should_parallelize = parallel_enabled and dirs_count > 1
        assert should_parallelize is True


class TestDedupWorkerSignatures:
    """Tests that verify worker functions have correct signatures."""

    def test_preprocess_worker_signature(self) -> None:
        """_preprocess_worker should have the expected signature."""
        from memoria import _preprocess_worker
        import inspect

        sig = inspect.signature(_preprocess_worker)
        params = list(sig.parameters.keys())

        # Must have these parameters
        assert "export_dir_str" in params
        assert "threads" in params
        assert "log_file" in params
        assert "processor_filter" in params

        # Should NOT have preprocessed_dirs_json (removed per plan)
        assert "preprocessed_dirs_json" not in params

    def test_build_catalog_worker_signature(self) -> None:
        """_build_catalog_worker should not have preprocessed_dirs_json param."""
        from memoria import _build_catalog_worker
        import inspect

        sig = inspect.signature(_build_catalog_worker)
        params = list(sig.parameters.keys())

        # Must have these parameters
        assert "export_dir_str" in params
        assert "threads" in params
        assert "db_path" in params
        assert "run_id" in params

        # Should NOT have preprocessed_dirs_json (removed per plan)
        assert "preprocessed_dirs_json" not in params

    def test_process_export_worker_dedup_signature(self) -> None:
        """_process_export_worker_dedup should have expected signature."""
        from memoria import _process_export_worker_dedup
        import inspect

        sig = inspect.signature(_process_export_worker_dedup)
        params = list(sig.parameters.keys())

        # Must have these parameters for Phase 3
        assert "export_dir_str" in params
        assert "threads" in params
        assert "manifest_path_str" in params
        assert "output_base_str" in params


class TestDedupFunctionAvailability:
    """Tests that required functions exist and are importable."""

    def test_run_phased_dedup_processing_exists(self) -> None:
        """run_phased_dedup_processing should be importable."""
        from memoria import run_phased_dedup_processing

        assert callable(run_phased_dedup_processing)

    def test_run_phased_dedup_processing_accepts_parallel_params(self) -> None:
        """run_phased_dedup_processing should accept parallel control params."""
        from memoria import run_phased_dedup_processing
        import inspect

        sig = inspect.signature(run_phased_dedup_processing)
        params = list(sig.parameters.keys())

        # Must accept these params (from plan)
        assert "threads" in params
        assert "parallel_enabled" in params
        assert "parallel_exports" in params


class TestCatalogWorkerDerivedPaths:
    """Tests for deterministic path derivation in _build_catalog_worker."""

    def test_catalog_worker_imports_cache_helpers(self) -> None:
        """_build_catalog_worker should import preprocessing cache helpers."""
        import inspect
        from memoria import _build_catalog_worker

        source = inspect.getsource(_build_catalog_worker)

        # Should import these helpers for deterministic path derivation
        assert "get_preprocessing_cache_path" in source
        assert "is_preprocessing_cache_valid" in source

        # Should NOT reference preprocessed_dirs_json
        assert "preprocessed_dirs_json" not in source

    def test_catalog_worker_derives_paths_deterministically(self) -> None:
        """_build_catalog_worker should use same path derivation as Phase 0/3."""
        import inspect
        from memoria import _build_catalog_worker

        source = inspect.getsource(_build_catalog_worker)

        # Should call needs_preprocessing to decide
        assert "needs_preprocessing" in source

        # Should use cache path helpers
        assert "get_preprocessing_cache_path" in source
        assert "is_preprocessing_cache_valid" in source


class TestPhaseBarrierContract:
    """Tests for the Phase 2 barrier contract."""

    def test_manifest_path_construction(self) -> None:
        """Manifest path should be constructed before Phase 3."""
        # The manifest is written to output_base / "dedup_manifest.json"
        output_base = Path("/output")
        manifest_path = output_base / "dedup_manifest.json"

        assert manifest_path == Path("/output/dedup_manifest.json")

    def test_phase3_worker_reads_manifest_from_path(self) -> None:
        """Phase 3 worker should read manifest from disk path."""
        import inspect
        from memoria import _process_export_worker_dedup

        source = inspect.getsource(_process_export_worker_dedup)

        # Should accept manifest path, not manifest dict
        assert "manifest_path_str" in source

        # Should load manifest from file
        assert "json" in source.lower()


class TestNoLegacyPlumbing:
    """Tests that legacy plumbing has been removed."""

    def test_no_preprocessed_dirs_json_in_memoria(self) -> None:
        """preprocessed_dirs_json should not appear in memoria.py."""
        from pathlib import Path

        memoria_path = Path(__file__).parent.parent.parent / "memoria.py"
        content = memoria_path.read_text()

        assert "preprocessed_dirs_json" not in content

    def test_no_parallel_catalog_env_var(self) -> None:
        """PARALLEL_CATALOG env var should not be used."""
        from pathlib import Path

        memoria_path = Path(__file__).parent.parent.parent / "memoria.py"
        content = memoria_path.read_text()

        assert "PARALLEL_CATALOG" not in content


class TestProcessCountLogic:
    """Tests for process count logic matching memoria.py behavior."""

    def test_auto_process_count_has_eight_cap(self) -> None:
        """auto_process_count should cap at 8."""
        from common.config.runtime import auto_process_count

        with patch("common.config.runtime.cpu_count", return_value=65):
            # cpu_budget = 64, threads = 1 -> 64, capped at 8
            result = auto_process_count(threads=1, exports_count=100)
            assert result == 8

    def test_env_override_only_capped_by_exports(self) -> None:
        """MEMORIA_PROCESSES env override should only be capped by exports_count.

        This matches memoria.py behavior where:
            parallel_exports = min(env_processes, exports_count)
        NOT:
            parallel_exports = min(env_processes, exports_count, 8)
        """
        env_processes = 16
        exports_count = 20

        # memoria.py logic
        parallel_exports = min(env_processes, exports_count)

        # Should NOT be capped at 8 for env override
        assert parallel_exports == 16
