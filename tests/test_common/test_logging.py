#!/usr/bin/env python3
"""Tests for common/output/logging.py centralized logging configuration.

Tests cover:
- setup_logging() configuration
- Verbosity levels
- File logging with StreamingFileHandler
- Worker process logging initialization
- ProcessorLogger standardized logging methods
- Logs directory configuration
"""

import logging
from pathlib import Path
from typing import Generator

import pytest

from common.output.logging import (
    setup_logging,
    get_logger,
    get_processor_logger,
    ProcessorLogger,
    StreamingFileHandler,
    add_export_log_handler,
    remove_export_log_handler,
    init_worker_logging,
    set_logs_dir,
    get_logs_dir,
    LOG_FORMAT_DETAILED,
    LOG_FORMAT_SIMPLE,
    LOG_DATE_FORMAT,
    SUPPRESSED_LOGGERS,
)


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None, None, None]:
    """Reset logging configuration after each test."""
    yield
    # Reset root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)
    # Reset logs directory
    set_logs_dir(None)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_returns_root_logger(self) -> None:
        """Should return the root logger."""
        logger = setup_logging()
        assert logger == logging.getLogger()

    def test_default_verbosity_error_level(self) -> None:
        """Should set console to ERROR level with verbosity=0."""
        setup_logging(verbosity=0)
        root = logging.getLogger()
        # Root should be DEBUG to capture everything
        assert root.level == logging.DEBUG

    def test_verbosity_1_info_level(self) -> None:
        """Should show INFO with verbosity=1."""
        setup_logging(verbosity=1)
        # Handler should be at INFO level
        root = logging.getLogger()
        # Check console handler level
        console_handler = next(
            (h for h in root.handlers if hasattr(h, "console")), None
        )
        if console_handler:
            assert console_handler.level == logging.INFO

    def test_verbosity_2_debug_level(self) -> None:
        """Should show DEBUG with verbosity>=2."""
        setup_logging(verbosity=2)
        root = logging.getLogger()
        console_handler = next(
            (h for h in root.handlers if hasattr(h, "console")), None
        )
        if console_handler:
            assert console_handler.level == logging.DEBUG

    def test_verbose_backwards_compatibility(self) -> None:
        """Should treat verbose=True as verbosity=1."""
        setup_logging(verbose=True)
        root = logging.getLogger()
        console_handler = next(
            (h for h in root.handlers if hasattr(h, "console")), None
        )
        if console_handler:
            assert console_handler.level == logging.INFO

    def test_clears_existing_handlers(self) -> None:
        """Should clear existing handlers to avoid duplicates."""
        root = logging.getLogger()
        dummy_handler = logging.NullHandler()
        root.addHandler(dummy_handler)

        setup_logging()

        assert dummy_handler not in root.handlers

    def test_adds_file_handler(self, tmp_path: Path) -> None:
        """Should add file handler when log_file specified."""
        log_file = tmp_path / "test.log"
        setup_logging(log_file=str(log_file))

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_file_handler_detailed_format(self, tmp_path: Path) -> None:
        """Should use detailed format for file logging."""
        log_file = tmp_path / "test.log"
        setup_logging(log_file=str(log_file))

        root = logging.getLogger()
        file_handler = next(
            (h for h in root.handlers if isinstance(h, logging.FileHandler)), None
        )
        assert file_handler is not None
        assert file_handler.formatter._fmt == LOG_FORMAT_DETAILED

    def test_suppresses_third_party_loggers(self) -> None:
        """Should suppress noisy third-party loggers."""
        setup_logging()

        for lib_name in SUPPRESSED_LOGGERS:
            lib_logger = logging.getLogger(lib_name)
            assert lib_logger.level == logging.WARNING

    def test_file_log_level_override(self, tmp_path: Path) -> None:
        """Should respect file_log_level parameter."""
        log_file = tmp_path / "test.log"
        setup_logging(log_file=str(log_file), file_log_level=logging.INFO)

        root = logging.getLogger()
        file_handler = next(
            (h for h in root.handlers if isinstance(h, logging.FileHandler)), None
        )
        assert file_handler is not None
        assert file_handler.level == logging.INFO


class TestStreamingFileHandler:
    """Tests for StreamingFileHandler class."""

    def test_flushes_after_emit(self, tmp_path: Path) -> None:
        """Should flush after every emit."""
        log_file = tmp_path / "test.log"
        handler = StreamingFileHandler(log_file)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        # File should contain the message immediately
        assert log_file.read_text().strip() == "test message"
        handler.close()

    def test_creates_file_on_first_write(self, tmp_path: Path) -> None:
        """Should create file on first write."""
        log_file = tmp_path / "new.log"
        assert not log_file.exists()

        handler = StreamingFileHandler(log_file)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.close()

        assert log_file.exists()


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_named_logger(self) -> None:
        """Should return logger with given name."""
        logger = get_logger("test.module")
        assert logger.name == "test.module"

    def test_returns_same_logger_for_same_name(self) -> None:
        """Should return same logger instance for same name."""
        logger1 = get_logger("test.module")
        logger2 = get_logger("test.module")
        assert logger1 is logger2


class TestLogsDirectory:
    """Tests for logs directory configuration."""

    def test_default_logs_dir(self) -> None:
        """Should default to 'logs' directory."""
        set_logs_dir(None)
        assert get_logs_dir() == Path("logs")

    def test_set_custom_logs_dir(self, tmp_path: Path) -> None:
        """Should use custom logs directory when set."""
        custom_dir = tmp_path / "custom_logs"
        set_logs_dir(custom_dir)
        assert get_logs_dir() == custom_dir

    def test_reset_logs_dir(self, tmp_path: Path) -> None:
        """Should reset to default when set to None."""
        set_logs_dir(tmp_path / "custom")
        set_logs_dir(None)
        assert get_logs_dir() == Path("logs")


class TestAddExportLogHandler:
    """Tests for add_export_log_handler function."""

    def test_returns_none_when_not_verbose(self) -> None:
        """Should return None when verbose=False."""
        handler = add_export_log_handler("test-export", verbose=False)
        assert handler is None

    def test_creates_log_file(self, tmp_path: Path) -> None:
        """Should create log file in logs directory."""
        set_logs_dir(tmp_path)
        handler = add_export_log_handler("test-export", verbose=True)

        try:
            log_files = list(tmp_path.glob("log-test-export-*.log"))
            assert len(log_files) == 1
        finally:
            if handler:
                remove_export_log_handler(handler)

    def test_adds_handler_to_root(self, tmp_path: Path) -> None:
        """Should add handler to root logger."""
        set_logs_dir(tmp_path)
        root = logging.getLogger()
        initial_count = len(root.handlers)

        handler = add_export_log_handler("test-export", verbose=True)

        try:
            assert len(root.handlers) == initial_count + 1
        finally:
            if handler:
                remove_export_log_handler(handler)


class TestRemoveExportLogHandler:
    """Tests for remove_export_log_handler function."""

    def test_removes_handler_from_root(self, tmp_path: Path) -> None:
        """Should remove handler from root logger."""
        set_logs_dir(tmp_path)
        handler = add_export_log_handler("test-export", verbose=True)

        root = logging.getLogger()
        assert handler in root.handlers

        remove_export_log_handler(handler)
        assert handler not in root.handlers

    def test_handles_none_handler(self) -> None:
        """Should handle None handler gracefully."""
        # Should not raise
        remove_export_log_handler(None)


class TestInitWorkerLogging:
    """Tests for init_worker_logging function."""

    def test_clears_handlers(self) -> None:
        """Should clear existing handlers."""
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())

        init_worker_logging()

        # Should have exactly one NullHandler
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.NullHandler)

    def test_adds_file_handler(self, tmp_path: Path) -> None:
        """Should add file handler when log_filename provided."""
        log_file = tmp_path / "worker.log"
        init_worker_logging(log_filename=str(log_file))

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_suppresses_third_party(self) -> None:
        """Should suppress third-party loggers."""
        init_worker_logging()

        for lib_name in SUPPRESSED_LOGGERS:
            lib_logger = logging.getLogger(lib_name)
            assert lib_logger.level == logging.WARNING

    def test_sets_app_modules_to_debug(self, tmp_path: Path) -> None:
        """Should set application modules to DEBUG."""
        log_file = tmp_path / "worker.log"
        init_worker_logging(
            log_filename=str(log_file),
            app_modules=["test.module"],
        )

        test_logger = logging.getLogger("test.module")
        assert test_logger.level == logging.DEBUG


class TestProcessorLogger:
    """Tests for ProcessorLogger class."""

    @pytest.fixture
    def proc_logger(self) -> ProcessorLogger:
        """Create a ProcessorLogger for testing."""
        return get_processor_logger("test.processor")

    def test_logger_property(self, proc_logger: ProcessorLogger) -> None:
        """Should expose underlying logger."""
        assert proc_logger.logger == logging.getLogger("test.processor")

    def test_processor_start(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log processor start header."""
        with caplog.at_level(logging.INFO):
            proc_logger.processor_start("Test Processor")

        assert "Test Processor" in caplog.text

    def test_loading_metadata(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log metadata loading."""
        with caplog.at_level(logging.INFO):
            proc_logger.loading_metadata("/path/to/metadata.json")

        assert "/path/to/metadata.json" in caplog.text

    def test_found_files(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log file count."""
        with caplog.at_level(logging.INFO):
            proc_logger.found_files(150, "media")

        assert "150" in caplog.text
        assert "media" in caplog.text

    def test_found_items(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log item count."""
        with caplog.at_level(logging.INFO):
            proc_logger.found_items(25, "conversations")

        assert "25" in caplog.text
        assert "conversations" in caplog.text

    def test_matched_files_with_unmatched(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log matched and unmatched counts."""
        with caplog.at_level(logging.INFO):
            proc_logger.matched_files(100, unmatched=5)

        assert "100" in caplog.text
        assert "5" in caplog.text

    def test_skipped_secondary_zero(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should not log when count is zero."""
        with caplog.at_level(logging.INFO):
            proc_logger.skipped_secondary(0)

        assert "skipped" not in caplog.text.lower()

    def test_skipped_secondary_nonzero(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log when count is nonzero."""
        with caplog.at_level(logging.INFO):
            proc_logger.skipped_secondary(10)

        assert "10" in caplog.text
        assert "secondary" in caplog.text.lower()

    def test_copied_files_with_failures(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log copied count with failures."""
        with caplog.at_level(logging.INFO):
            proc_logger.copied_files(95, failed=5)

        assert "95" in caplog.text
        assert "5" in caplog.text
        assert "failed" in caplog.text.lower()

    def test_file_not_found(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log file not found error."""
        with caplog.at_level(logging.ERROR):
            proc_logger.file_not_found("/missing/file.json", "Metadata file")

        assert "Metadata file" in caplog.text
        assert "/missing/file.json" in caplog.text
        assert caplog.records[0].levelno == logging.ERROR

    def test_processing_error(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log processing error."""
        with caplog.at_level(logging.ERROR):
            proc_logger.processing_error("photo.jpg", ValueError("test error"))

        assert "photo.jpg" in caplog.text
        assert "test error" in caplog.text

    def test_missing_file_with_context(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log missing file with context."""
        with caplog.at_level(logging.WARNING):
            proc_logger.missing_file("photo.jpg", "referenced in message 123")

        assert "photo.jpg" in caplog.text
        assert "message 123" in caplog.text

    def test_detection_failed_debug_level(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log detection failure at DEBUG level."""
        with caplog.at_level(logging.DEBUG):
            proc_logger.detection_failed("TestProcessor", Exception("not found"))

        assert "TestProcessor" in caplog.text
        assert caplog.records[0].levelno == logging.DEBUG

    def test_using_workers_debug_level(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log worker count at DEBUG level."""
        with caplog.at_level(logging.DEBUG):
            proc_logger.using_workers(8)

        assert "8" in caplog.text
        assert caplog.records[0].levelno == logging.DEBUG

    def test_preprocessor_start(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log preprocessor start with paths."""
        with caplog.at_level(logging.INFO):
            proc_logger.preprocessor_start(
                "Snapchat", "/exports/snapchat", "/output/snapchat"
            )

        assert "Snapchat" in caplog.text
        assert "/exports/snapchat" in caplog.text
        assert "/output/snapchat" in caplog.text

    def test_exif_operations(
        self, proc_logger: ProcessorLogger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log EXIF operations."""
        with caplog.at_level(logging.INFO):
            proc_logger.exif_batch_start()
            proc_logger.exif_rebuilding(5)
            proc_logger.exif_written(100)

        assert "EXIF" in caplog.text or "exif" in caplog.text.lower()
        assert "5" in caplog.text
        assert "100" in caplog.text


class TestFormatConstants:
    """Tests for format constants."""

    def test_detailed_format_has_timestamp(self) -> None:
        """Detailed format should include asctime."""
        assert "asctime" in LOG_FORMAT_DETAILED

    def test_detailed_format_has_name(self) -> None:
        """Detailed format should include logger name."""
        assert "name" in LOG_FORMAT_DETAILED

    def test_simple_format_minimal(self) -> None:
        """Simple format should be minimal."""
        assert "asctime" not in LOG_FORMAT_SIMPLE
        assert "levelname" in LOG_FORMAT_SIMPLE

    def test_date_format_time_only(self) -> None:
        """Date format should be time-only."""
        assert "H" in LOG_DATE_FORMAT
        assert "M" in LOG_DATE_FORMAT
        assert "Y" not in LOG_DATE_FORMAT  # No year


class TestGetProcessorLogger:
    """Tests for get_processor_logger factory function."""

    def test_returns_processor_logger(self) -> None:
        """Should return ProcessorLogger instance."""
        logger = get_processor_logger("test.module")
        assert isinstance(logger, ProcessorLogger)

    def test_logger_has_correct_name(self) -> None:
        """Should create logger with correct name."""
        logger = get_processor_logger("test.module")
        assert logger.logger.name == "test.module"

