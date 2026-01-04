"""Processing utilities for parallel execution, caching, and temp directories.

All batch processing functions use threading (ThreadPoolExecutor) for within-export
parallelism. Cross-export parallelism uses ProcessPoolExecutor directly in memoria.py.
"""

from common.processing.batch import (
    process_batches_threaded,
    process_items_threaded,
    process_parallel_threaded,
)
from common.processing.cache import (
    get_preprocessing_cache_path,
    is_preprocessing_cache_valid,
    cleanup_preprocessing_cache,
)
from common.processing.temp import temp_processing_directory

__all__ = [
    # Batch processing (threading - for within-export work)
    "process_batches_threaded",
    "process_items_threaded",
    "process_parallel_threaded",
    # Cache management
    "get_preprocessing_cache_path",
    "is_preprocessing_cache_valid",
    "cleanup_preprocessing_cache",
    # Temp directories
    "temp_processing_directory",
]
