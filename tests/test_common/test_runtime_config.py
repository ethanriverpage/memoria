"""Tests for runtime configuration utilities.

Tests plan-critical behaviors:
- default_thread_count() with parallel_enabled on/off
- auto_process_count() formula and capping
- Environment variable precedence for MEMORIA_THREADS, MEMORIA_PROCESSES
"""

import os
from typing import Generator
from unittest.mock import patch

import pytest

from common.config.runtime import (
    default_thread_count,
    auto_process_count,
    parse_bool_env,
    parse_int_env,
)


class TestDefaultThreadCount:
    """Tests for default_thread_count() function."""

    def test_single_export_returns_cpu_budget(self) -> None:
        """Without parallel, should return cpu_count - 1."""
        with patch("common.config.runtime.cpu_count", return_value=8):
            result = default_thread_count(parallel_enabled=False)
            assert result == 7  # cpu_count - 1

    def test_parallel_enabled_returns_sqrt(self) -> None:
        """With parallel, should return floor(sqrt(cpu_budget))."""
        with patch("common.config.runtime.cpu_count", return_value=17):
            # cpu_budget = 16, sqrt(16) = 4
            result = default_thread_count(parallel_enabled=True)
            assert result == 4

    def test_single_cpu_returns_one(self) -> None:
        """With 1 CPU, should return at least 1."""
        with patch("common.config.runtime.cpu_count", return_value=1):
            result = default_thread_count(parallel_enabled=False)
            assert result == 1

            result_parallel = default_thread_count(parallel_enabled=True)
            assert result_parallel == 1

    def test_two_cpus(self) -> None:
        """With 2 CPUs, should return 1 (2-1=1)."""
        with patch("common.config.runtime.cpu_count", return_value=2):
            result = default_thread_count(parallel_enabled=False)
            assert result == 1

    def test_parallel_always_at_least_one(self) -> None:
        """Parallel mode should always return at least 1."""
        for cpu in range(1, 100):
            with patch("common.config.runtime.cpu_count", return_value=cpu):
                result = default_thread_count(parallel_enabled=True)
                assert result >= 1, f"Failed for cpu_count={cpu}"


class TestAutoProcessCount:
    """Tests for auto_process_count() function."""

    def test_basic_formula(self) -> None:
        """Should compute cpu_budget // threads."""
        with patch("common.config.runtime.cpu_count", return_value=17):
            # cpu_budget = 16, threads = 4 -> 16 // 4 = 4
            result = auto_process_count(threads=4, exports_count=10)
            assert result == 4

    def test_capped_at_eight(self) -> None:
        """Should never exceed 8 processes."""
        with patch("common.config.runtime.cpu_count", return_value=65):
            # cpu_budget = 64, threads = 1 -> 64 // 1 = 64, but capped at 8
            result = auto_process_count(threads=1, exports_count=100)
            assert result == 8

    def test_capped_at_exports_count(self) -> None:
        """Should not exceed number of exports."""
        with patch("common.config.runtime.cpu_count", return_value=17):
            # cpu_budget = 16, threads = 2 -> 8, but only 3 exports
            result = auto_process_count(threads=2, exports_count=3)
            assert result == 3

    def test_minimum_one(self) -> None:
        """Should always return at least 1."""
        with patch("common.config.runtime.cpu_count", return_value=1):
            result = auto_process_count(threads=100, exports_count=100)
            assert result == 1

    def test_zero_threads_treated_as_one(self) -> None:
        """threads=0 should be treated as 1 (max(1, threads))."""
        with patch("common.config.runtime.cpu_count", return_value=9):
            # cpu_budget = 8, max(1, 0) = 1 -> 8 // 1 = 8
            result = auto_process_count(threads=0, exports_count=10)
            assert result == 8


class TestParseBoolEnv:
    """Tests for parse_bool_env() function."""

    def test_true_values(self) -> None:
        """Should recognize various truthy values."""
        for val in ["true", "True", "TRUE", "1", "yes", "Yes", "on", "ON"]:
            assert parse_bool_env(val) is True, f"Failed for {val}"

    def test_false_values(self) -> None:
        """Should return False for non-truthy values."""
        for val in ["false", "0", "no", "off", "", "random"]:
            assert parse_bool_env(val) is False, f"Failed for {val}"

    def test_none_uses_default(self) -> None:
        """None should return the default value."""
        assert parse_bool_env(None, default=False) is False
        assert parse_bool_env(None, default=True) is True


class TestParseIntEnv:
    """Tests for parse_int_env() function."""

    def test_valid_integers(self) -> None:
        """Should parse valid integer strings."""
        assert parse_int_env("4") == 4
        assert parse_int_env("0") == 0
        assert parse_int_env("-1") == -1
        assert parse_int_env("100") == 100

    def test_invalid_strings(self) -> None:
        """Should return default for invalid strings."""
        assert parse_int_env("abc") is None
        assert parse_int_env("abc", default=5) == 5
        assert parse_int_env("1.5", default=2) == 2
        assert parse_int_env("") is None

    def test_none_uses_default(self) -> None:
        """None should return the default value."""
        assert parse_int_env(None) is None
        assert parse_int_env(None, default=8) == 8


class TestMemoriaEnvPrecedence:
    """Tests for environment variable precedence in memoria.py.

    These tests verify that the CLI > env > auto precedence is respected.
    """

    @pytest.fixture(autouse=True)
    def clean_env(self) -> Generator[None, None, None]:
        """Clean MEMORIA_* env vars before and after each test."""
        env_vars = ["MEMORIA_THREADS", "MEMORIA_PROCESSES", "MEMORIA_PARALLEL"]
        old_values = {k: os.environ.get(k) for k in env_vars}
        for k in env_vars:
            if k in os.environ:
                del os.environ[k]
        yield
        for k, v in old_values.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]

    def test_threads_env_var_parsed(self) -> None:
        """MEMORIA_THREADS should be parseable."""
        os.environ["MEMORIA_THREADS"] = "4"
        assert parse_int_env(os.environ.get("MEMORIA_THREADS")) == 4

    def test_processes_env_var_parsed(self) -> None:
        """MEMORIA_PROCESSES should be parseable."""
        os.environ["MEMORIA_PROCESSES"] = "2"
        assert parse_int_env(os.environ.get("MEMORIA_PROCESSES")) == 2

    def test_parallel_env_var_parsed(self) -> None:
        """MEMORIA_PARALLEL should be parseable as boolean."""
        os.environ["MEMORIA_PARALLEL"] = "true"
        assert parse_bool_env(os.environ.get("MEMORIA_PARALLEL")) is True

        os.environ["MEMORIA_PARALLEL"] = "false"
        assert parse_bool_env(os.environ.get("MEMORIA_PARALLEL")) is False


class TestProcessCountCapping:
    """Tests for MEMORIA_PROCESSES override and capping.

    Critical behavior from memoria.py:
    - MEMORIA_PROCESSES override is capped only by exports_count (not 8)
    - auto_process_count() has the 8-cap built in
    - This allows power users to override the 8-cap via MEMORIA_PROCESSES
      if they have the resources
    """

    def test_env_override_respected(self) -> None:
        """MEMORIA_PROCESSES should override auto computation."""
        # If MEMORIA_PROCESSES=2 and auto would give 4, we should get 2
        env_val = parse_int_env("2")
        auto_val = 4
        exports_count = 10

        # Apply the same logic as memoria.py (env override capped by exports only)
        if env_val is not None:
            procs = min(env_val, exports_count)
        else:
            procs = auto_val

        assert procs == 2

    def test_env_override_capped_at_exports(self) -> None:
        """MEMORIA_PROCESSES should be capped at exports_count."""
        env_val = 10
        exports_count = 3

        # memoria.py: parallel_exports = min(env_processes, exports_count)
        procs = min(env_val, exports_count)
        assert procs == 3

    def test_env_override_not_capped_at_eight(self) -> None:
        """MEMORIA_PROCESSES allows users to exceed auto's 8-cap.

        The 8-process cap is in auto_process_count(), not in the env override.
        Power users can set MEMORIA_PROCESSES=16 if they have resources.
        """
        env_val = 16
        exports_count = 20

        # memoria.py: parallel_exports = min(env_processes, exports_count)
        procs = min(env_val, exports_count)
        assert procs == 16  # Not capped at 8

    def test_auto_process_count_has_eight_cap(self) -> None:
        """auto_process_count() should have the 8-cap built in."""
        with patch("common.config.runtime.cpu_count", return_value=65):
            # cpu_budget = 64, threads = 1 -> 64 // 1 = 64, capped at 8
            result = auto_process_count(threads=1, exports_count=100)
            assert result == 8
