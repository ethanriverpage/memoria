# DRY Principle Violations

This document catalogs areas of the codebase where the DRY (Don't Repeat Yourself) principle is being violated, along with recommended refactoring strategies.

## Overview

The media-processing codebase has grown organically with 10+ processors, each following similar patterns. While the code is functional, significant duplication exists that increases maintenance burden and risk of inconsistent behavior.

---

## 1. `build_catalog()` Method

**Location**: All `processor.py` files  
**Duplication**: ~50 lines repeated across 10+ files

### Current Pattern

Every processor has an almost identical `build_catalog()` implementation:

```python
@staticmethod
def build_catalog(input_dir: str, **kwargs) -> Dict[str, Any]:
    input_path = Path(input_dir)
    workers = kwargs.get("workers", 4)
    config = get_dedup_config()

    catalog = {
        "processor": "PROCESSOR_NAME",
        "input_dir": str(input_path),
        "files": [],
    }
    
    # Find media files (varies by processor)
    media_files = [...]
    
    def process_file(file_path: Path) -> Optional[Dict[str, Any]]:
        try:
            content_hash = compute_content_hash(file_path)
            perceptual_hash = None
            dimensions = None
            if config.use_perceptual:
                # Get hash and dimensions in one file open
                perceptual_hash, dimensions = compute_perceptual_hash_with_dims(file_path)
            else:
                dimensions = get_media_dimensions(file_path)
            return {
                "source_path": str(file_path),
                "relative_path": str(file_path.relative_to(input_path)),
                "content_hash": content_hash,
                "perceptual_hash": perceptual_hash,
                "file_size": file_path.stat().st_size,
                "dimensions": dimensions,
                "timestamp": None,
                "metadata": {},
            }
        except Exception as e:
            logger.warning(f"Failed to catalog {file_path}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_file, fp): fp for fp in media_files}
        for future in futures_progress(futures, PHASE_CATALOG, "Hashing files", unit="file"):
            result = future.result()
            if result:
                catalog["files"].append(result)

    return catalog
```

### Affected Files

- `processors/discord/processor.py`
- `processors/google_chat/processor.py`
- `processors/google_photos/processor.py`
- `processors/google_voice/processor.py`
- `processors/imessage/processor.py`
- `processors/imessage_imazing/processor.py`
- `processors/instagram_messages/processor.py`
- `processors/instagram_public_media/processor.py`
- `processors/instaloader/processor.py`
- `processors/snapchat_memories/processor.py`
- `processors/snapchat_messages/processor.py`

### Recommended Refactoring

Create a shared utility in `common/catalog.py`:

```python
def catalog_media_files(
    processor_name: str,
    input_path: Path,
    media_files: List[Path],
    workers: int = 4,
    metadata_extractor: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Build a catalog of media files with hashes and metadata.
    
    Args:
        processor_name: Name of the processor for the catalog
        input_path: Base input path for relative path calculation
        media_files: List of media file paths to catalog
        workers: Number of parallel workers
        metadata_extractor: Optional function to extract additional metadata
    
    Returns:
        Catalog dictionary with processor name and file entries
    """
    ...
```

---

## 2. `_process_working_directory()` Method

**Location**: Select `processor.py` files  
**Duplication**: ~100 lines repeated across 6 files

### Current Pattern

```python
def _process_working_directory(working_dir, output_dir, workers, dedup_manifest=None):
    # 1. Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")
    media_dir = os.path.join(working_dir, "media")
    
    # 2. Validation (identical across all processors)
    if not os.path.exists(metadata_file):
        logger.error(f"Metadata file not found: {metadata_file}")
        return
    
    if not os.path.exists(media_dir):
        logger.error(f"Media directory not found: {media_dir}")
        return
    
    # 3. Load metadata
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata_json = json.load(f)
    
    # 4. Extract export info
    export_info = metadata_json.get("export_info", {})
    
    # 5. Determine workers
    num_workers = workers if workers is not None else default_worker_count()
    
    # 6. Filter dedup files (identical pattern)
    # 7. Pre-generate filenames
    # 8. Process files in parallel
    # 9. Batch EXIF operations
    # 10. Print summary
```

### Affected Files

- `processors/discord/processor.py`
- `processors/google_chat/processor.py`
- `processors/google_photos/processor.py`
- `processors/imessage/processor.py`
- `processors/instagram_messages/processor.py`
- `processors/instagram_public_media/processor.py`

Note: Other processors (snapchat_memories, snapchat_messages, instaloader, imessage_imazing, google_voice) handle processing differently or inline within `process_logic()`.

### Recommended Refactoring

Create a `ProcessingContext` class or use a template method pattern in `ProcessorBase`:

```python
class ProcessorBase:
    def process_working_directory(
        self,
        working_dir: Path,
        output_dir: Path,
        workers: Optional[int],
        dedup_manifest: Optional[Dict],
    ) -> bool:
        """Template method for processing a preprocessed directory."""
        context = self._load_working_directory(working_dir)
        if not context:
            return False
        
        files = self._filter_files(context, dedup_manifest)
        filenames = self._generate_filenames(files, context)
        results = self._process_files(files, filenames, output_dir, workers)
        self._finalize(results, output_dir)
        return True
```

---

## 3. `process_media_batch()` Worker Function

**Location**: 6+ processor files  
**Duplication**: ~80 lines repeated per file

### Current Pattern

```python
def process_media_batch(batch_args):
    # Phase 1: Copy all files
    file_paths = []
    file_info = []
    for args_tuple in batch_args:
        # Unpack (varies slightly by processor)
        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)
        
        if not os.path.exists(media_path):
            logger.warning(f"Media file not found: {media_path}")
            continue
        
        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append((output_path, ...))
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")
    
    if not file_paths:
        return [(False, True, False)] * len(batch_args)
    
    # Phase 2: Batch validate and rebuild (identical)
    corrupted_files = batch_validate_exif(file_paths)
    if corrupted_files:
        batch_rebuild_exif(list(corrupted_files))
    
    # Phase 3: Batch read and write metadata
    existing_metadata_map = batch_read_existing_metadata(file_paths)
    batch_write_metadata_XXX(file_info, existing_metadata_map)  # Only this varies
    
    # Phase 4: Update timestamps and compile results (identical)
    results = []
    for output_path, ... in file_info:
        update_file_timestamps(output_path, timestamp_str, FORMAT)
        exif_rebuilt = output_path in corrupted_files
        results.append((True, False, exif_rebuilt))
    
    while len(results) < len(batch_args):
        results.append((False, True, False))
    
    return results
```

### Affected Files

- `processors/google_chat/processor.py`
- `processors/google_photos/processor.py`
- `processors/instagram_messages/processor.py`
- `processors/instagram_public_media/processor.py`
- `processors/instaloader/processor.py`
- `processors/snapchat_memories/processor.py`

### Recommended Refactoring

Create a generic batch processor in `common/processing.py`:

```python
def create_batch_processor(
    metadata_writer: Callable,
    timestamp_format: str,
    timestamp_extractor: Callable,
) -> Callable:
    """
    Factory function to create a batch processor with processor-specific behavior.
    
    Args:
        metadata_writer: Function like batch_write_metadata_google_chat
        timestamp_format: strptime format for timestamps
        timestamp_extractor: Function to extract timestamp from metadata
    
    Returns:
        A process_media_batch function tailored to the processor
    """
    def process_media_batch(batch_args):
        # Shared implementation with injected dependencies
        ...
    
    return process_media_batch
```

---

## 4. `generate_unique_filename()` Function

**Location**: 6 processor files (plus 1 different variant in discord/preprocess.py)  
**Duplication**: ~30 lines repeated per file

### Current Pattern

```python
def generate_unique_filename(
    data, identifier, export_username, extension, used_filenames
):
    # Parse date (format varies by processor)
    date_str = data["timestamp"]
    if date_str is None:
        date_key = "00000000"
    else:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        date_key = date_obj.strftime("%Y%m%d")
    
    # Generate base filename (prefix varies by processor)
    base_filename = f"prefix-{export_username}-{identifier}-{date_key}{extension}"
    
    # Check uniqueness (identical logic)
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename
    
    # Add sequence number (starting number varies: 1 or 2)
    sequence = 2
    while True:
        filename = f"prefix-{export_username}-{identifier}-{date_key}_{sequence}{extension}"
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1
```

### Affected Files

- `processors/google_chat/processor.py`
- `processors/google_voice/processor.py`
- `processors/instagram_messages/processor.py`
- `processors/instaloader/processor.py`
- `processors/instagram_public_media/processor.py`
- `processors/snapchat_memories/processor.py`

Note: `processors/discord/preprocess.py` has a `generate_unique_filename()` method but it serves a different purpose (generating unique filenames for downloaded attachments during preprocessing, not for final output files).

### Inconsistencies

- Some processors start sequence at 1, others at 2
- Timestamp formats vary but parsing logic is duplicated
- Prefix patterns follow similar structure but are hardcoded

### Recommended Refactoring

Create a `FilenameGenerator` class in `common/utils.py`:

```python
class FilenameGenerator:
    """Generate unique filenames with consistent deduplication logic."""
    
    def __init__(self, prefix: str, timestamp_format: str = "%Y-%m-%d %H:%M:%S"):
        self.prefix = prefix
        self.timestamp_format = timestamp_format
        self.used_filenames: Set[str] = set()
    
    def generate(
        self,
        timestamp_str: Optional[str],
        username: str,
        identifier: str,
        extension: str,
    ) -> str:
        """Generate a unique filename."""
        date_key = self._parse_date(timestamp_str)
        base = f"{self.prefix}-{username}-{identifier}-{date_key}{extension}"
        return self._ensure_unique(base, extension)
    
    def _parse_date(self, timestamp_str: Optional[str]) -> str:
        if not timestamp_str:
            return "00000000"
        try:
            dt = datetime.strptime(timestamp_str, self.timestamp_format)
            return dt.strftime("%Y%m%d")
        except ValueError:
            return "00000000"
    
    def _ensure_unique(self, base: str, extension: str) -> str:
        if base not in self.used_filenames:
            self.used_filenames.add(base)
            return base
        
        stem = base.rsplit(extension, 1)[0]
        sequence = 2
        while True:
            candidate = f"{stem}_{sequence}{extension}"
            if candidate not in self.used_filenames:
                self.used_filenames.add(candidate)
                return candidate
            sequence += 1
```

---

## 5. `process_logic()` Entry Point

**Location**: All `processor.py` files  
**Duplication**: ~40 lines repeated per file

### Current Pattern

```python
def process_logic(input_dir, output_dir, temp_dir, verbose, workers, dedup_manifest):
    # Dependency check (identical)
    if not check_exiftool():
        print_exiftool_error()
        return
    
    # Header logging (only processor name varies)
    logger.info("PROCESSOR_NAME")
    logger.info("=" * 50)
    
    # Preprocessing decision (identical structure)
    if is_preprocessed_directory(input_dir):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir, output_dir, workers, dedup_manifest)
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing...")
        
        with temp_processing_directory(temp_dir, "prefix") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")
            
            preprocessor = XXXPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=Path(output_dir),
            )
            preprocessor.process()
            
            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_working_directory(str(temp_dir_path), output_dir, workers, dedup_manifest)
```

### Recommended Refactoring

Move to `ProcessorBase` as a template method:

```python
class ProcessorBase:
    @classmethod
    def run_processing_pipeline(
        cls,
        input_dir: str,
        output_dir: str,
        temp_dir: str,
        workers: Optional[int],
        dedup_manifest: Optional[Dict],
    ) -> bool:
        """Standard processing pipeline for all processors."""
        if not cls._check_dependencies():
            return False
        
        cls._log_header()
        
        if is_preprocessed_directory(input_dir):
            return cls._process_preprocessed(input_dir, output_dir, workers, dedup_manifest)
        else:
            return cls._process_raw(input_dir, output_dir, temp_dir, workers, dedup_manifest)
```

---

## 6. Preprocessor Common Methods

**Location**: All `preprocess.py` files  
**Duplication**: ~100 lines repeated across 8+ files

### Duplicated Methods

#### `log_message()` - Identical in all preprocessors

```python
def log_message(self, category: str, message: str, details: str = "") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {category}: {message}"
    if details:
        entry += f" ({details})"
    with self.log_lock:
        self.log_entries.append(entry)
```

#### `save_log()` - Identical structure

```python
def save_log(self) -> None:
    if not self.log_entries:
        return
    
    try:
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("PROCESSOR_NAME Preprocessing Log\n")
            f.write("=" * 80 + "\n")
            f.write(f"Export: {self.export_path}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")
            for entry in self.log_entries:
                f.write(entry + "\n")
            f.write(f"\nTotal logged issues: {len(self.log_entries)}\n")
        logger.info("Saved preprocessing log to %s", self.log_file)
    except Exception as e:
        logger.warning("Failed to save log file: %s", e)
```

#### `validate_export()` - Similar structure

#### `print_statistics()` - Identical pattern

### Recommended Refactoring

Create a `BasePreprocessor` class:

```python
class BasePreprocessor:
    """Base class for all export preprocessors with common functionality."""
    
    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        self.export_path = Path(export_path)
        self.output_dir = Path(output_dir) if output_dir else self.export_path
        self.media_output_dir = self.output_dir / "media"
        self.metadata_file = self.output_dir / "metadata.json"
        self.log_file = self.output_dir / "preprocessing.log"
        self.final_output_dir = Path(final_output_dir) if final_output_dir else self.output_dir
        
        self.workers = workers if workers is not None else default_worker_count()
        self.banned_filter = BannedFilesFilter()
        self.failure_tracker = FailureTracker(
            processor_name=self.get_processor_name(),
            export_directory=str(export_path),
        )
        
        self.stats_lock = Lock()
        self.log_lock = Lock()
        self.log_entries: List[str] = []
        self.stats: Dict[str, int] = self._init_stats()
    
    @abstractmethod
    def get_processor_name(self) -> str:
        """Return the processor name for logging."""
        pass
    
    @abstractmethod
    def _init_stats(self) -> Dict[str, int]:
        """Initialize statistics dictionary."""
        pass
    
    @abstractmethod
    def validate_export(self) -> bool:
        """Validate export directory structure."""
        pass
    
    # Common methods that don't need to be overridden
    def log_message(self, category: str, message: str, details: str = "") -> None:
        ...
    
    def save_log(self) -> None:
        ...
    
    def print_statistics(self) -> None:
        ...
```

---

## 7. Dedup Filtering Pattern

**Location**: All `processor.py` files  
**Duplication**: ~15 lines repeated 11 times across 10 files

### Current Pattern

```python
if dedup_manifest:
    original_count = len(media_files)
    filtered_files = []
    for f in media_files:
        content_hash = compute_content_hash(f)
        if is_file_primary_by_hash(dedup_manifest, content_hash, "PROCESSOR_NAME"):
            filtered_files.append(f)
    media_files = filtered_files
    skipped_count = original_count - len(media_files)
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} secondary files (dedup)")
```

### Recommended Refactoring

Add to `common/dedup_service.py`:

```python
def filter_primary_files(
    files: List[Path],
    dedup_manifest: Dict[str, Any],
    processor_name: str,
) -> Tuple[List[Path], int]:
    """
    Filter files to only include primary copies based on dedup manifest.
    
    Args:
        files: List of file paths to filter
        dedup_manifest: Deduplication manifest
        processor_name: Name of the calling processor
    
    Returns:
        Tuple of (filtered files list, count of skipped files)
    """
    if not dedup_manifest:
        return files, 0
    
    filtered = []
    for f in files:
        content_hash = compute_content_hash(f)
        if is_file_primary_by_hash(dedup_manifest, content_hash, processor_name):
            filtered.append(f)
    
    skipped = len(files) - len(filtered)
    if skipped > 0:
        logger.info(f"Skipped {skipped} secondary files (dedup)")
    
    return filtered, skipped
```

---

## Priority Matrix

| Priority | Violation | Lines Saved | Files Affected | Complexity |
|----------|-----------|-------------|----------------|------------|
| HIGH | `process_media_batch()` | ~400 | 6 | Medium |
| HIGH | `build_catalog()` | ~500 | 11 | Low |
| HIGH | `generate_unique_filename()` | ~200 | 6 | Low |
| MEDIUM | Preprocessor base class | ~800 | 8 | Medium |
| MEDIUM | `_process_working_directory()` | ~600 | 6 | High |
| MEDIUM | Dedup filtering | ~150 | 10 | Low |
| LOW | `process_logic()` template | ~400 | 11 | Medium |

---

## Implementation Strategy

1. **Phase 1 - Low-hanging fruit** (1-2 days)
   - Create `FilenameGenerator` class
   - Create `filter_primary_files()` utility
   - Update one processor as proof of concept

2. **Phase 2 - Core refactoring** (3-5 days)
   - Create `catalog_media_files()` utility
   - Create generic `process_media_batch()` factory
   - Migrate all processors to use new utilities

3. **Phase 3 - Architecture improvements** (5-7 days)
   - Create `BasePreprocessor` class
   - Refactor `ProcessorBase` with template methods
   - Migrate all preprocessors

4. **Phase 4 - Testing and validation** (2-3 days)
   - Ensure all existing tests pass
   - Add tests for new shared utilities
   - Performance validation

---

## Notes

- All refactoring should maintain backward compatibility
- Consider feature flags to gradually roll out changes
- Ensure thread safety is preserved in shared utilities
- Document any behavior changes in CHANGELOG
