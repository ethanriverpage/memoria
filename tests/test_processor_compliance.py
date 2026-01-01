#!/usr/bin/env python3
"""Contract/compliance tests for all processors.

These tests are intentionally lightweight and only validate the processor
interface and invariants (not processing output correctness).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Set, Type

from common.output.paths import OutputInfo
from processors.base import ProcessorBase


def _get_registered_processor_classes(processor_registry) -> Set[Type[ProcessorBase]]:
    processors = set(processor_registry.get_all_processors())
    # Safety: ensure all are ProcessorBase subclasses
    for cls in processors:
        assert issubclass(cls, ProcessorBase)
        assert cls is not ProcessorBase
    return processors


def test_all_11_processors_are_registered(processor_registry) -> None:
    """Ensure our suite covers every processor (no silent skips)."""

    expected_class_names = {
        "DiscordProcessor",
        "GoogleChatProcessor",
        "GooglePhotosProcessor",
        "GoogleVoiceProcessor",
        "IMessageProcessor",
        "ImazingProcessor",
        "InstagramMessagesProcessor",
        "InstagramPublicMediaProcessor",
        "InstaloaderProcessor",
        "SnapchatMemoriesProcessor",
        "SnapchatMessagesProcessor",
    }

    processors = _get_registered_processor_classes(processor_registry)
    actual_class_names = {cls.__name__ for cls in processors}

    assert actual_class_names == expected_class_names


def test_processor_contract(processor_registry, tmp_path: Path) -> None:
    processors = sorted(
        _get_registered_processor_classes(processor_registry), key=lambda c: c.__name__
    )

    for cls in processors:
        # ---------------------------------------------------------------------
        # Required callable surface
        # ---------------------------------------------------------------------
        assert callable(cls.detect)
        assert callable(cls.get_name)
        assert callable(cls.get_priority)
        assert callable(cls.get_output_info)
        assert callable(cls.process)

        # Detect should not throw on an existing (but empty) directory.
        detect_result = cls.detect(tmp_path)
        assert isinstance(detect_result, bool)

        # ---------------------------------------------------------------------
        # Name / priority invariants
        # ---------------------------------------------------------------------
        name = cls.get_name()
        assert isinstance(name, str)
        assert name.strip() != ""

        priority = cls.get_priority()
        assert isinstance(priority, int)
        assert 1 <= priority <= 100

        # ---------------------------------------------------------------------
        # OutputInfo invariants
        # ---------------------------------------------------------------------
        output_info = cls.get_output_info()
        assert isinstance(output_info, OutputInfo)
        assert isinstance(output_info.platform, str) and output_info.platform.strip() != ""
        assert (
            isinstance(output_info.username_prefix, str)
            and output_info.username_prefix.strip() != ""
        )
        if output_info.service is not None:
            assert isinstance(output_info.service, str)
            assert output_info.service.strip() != ""

        # ---------------------------------------------------------------------
        # Dependency declarations are well-formed
        # ---------------------------------------------------------------------
        deps = cls.get_required_dependencies()
        assert isinstance(deps, list)
        allowed = set(ProcessorBase._DEPENDENCY_CHECKERS.keys())
        assert all(isinstance(d, str) and d in allowed for d in deps)

        # ---------------------------------------------------------------------
        # Capability declarations imply required overrides
        # (also enforced in ProcessorBase.__init_subclass__, but tested here too)
        # ---------------------------------------------------------------------
        assert isinstance(cls.supports_dedup(), bool)
        assert isinstance(cls.supports_consolidation(), bool)

        if cls.supports_dedup():
            assert cls.build_catalog is not ProcessorBase.build_catalog

        if cls.supports_consolidation():
            assert cls.process_consolidated is not ProcessorBase.process_consolidated

        # ---------------------------------------------------------------------
        # Signature sanity checks (avoids accidental interface drift)
        # ---------------------------------------------------------------------
        detect_sig = inspect.signature(cls.detect)
        assert len(detect_sig.parameters) == 1  # input_path


