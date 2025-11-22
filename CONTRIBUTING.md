# Contributing to Memoria

Thank you for your interest in contributing to Memoria! This document provides guidelines and instructions for setting up your development environment and contributing to the project.

## Development Setup

### Prerequisites

Ensure you have the following installed on your system:

**Required:**
- Python 3.7 or higher
- exiftool ([installation instructions](README.md#system-requirements))
- ffmpeg ([installation instructions](README.md#system-requirements))
- libmagic ([installation instructions](README.md#system-requirements))

**Optional (for Immich integration):**
- Immich CLI: `npm i -g @immich/cli`

### Setting Up Your Development Environment

1. **Clone the repository:**

```bash
git clone https://github.com/yourusername/memoria.git
cd memoria
```

2. **Create and activate a virtual environment:**

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**

```bash
pip install -r requirements.txt
```

4. **Install in development mode:**

```bash
pip install -e .
```

This makes the `common` and `processors` modules importable and allows you to test changes immediately.

5. **Verify installation:**

```bash
# Test system dependencies
exiftool -ver
ffmpeg -version

# Test Python imports
python3 -c "from common.filter_banned_files import BannedFilesFilter; print('Success!')"

# Run the main script
./memoria.py --list-processors
```

### Environment Configuration

Create a `.env` file in the project root for local development (see `.env.example` for reference):

```bash
# Optional: Immich integration
IMMICH_INSTANCE_URL=https://your-immich-server.com
IMMICH_API_KEY=your_api_key_here
IMMICH_UPLOAD_CONCURRENCY=4

# Optional: Custom temp directory
TEMP_DIR=../pre

# Optional: Keep temp files for debugging
DISABLE_TEMP_CLEANUP=false
```

## Code Style and Standards

### Python Style

We follow these coding standards:

1. **Formatting**: Use [Black](https://github.com/psf/black) for code formatting
   ```bash
   pip install black
   black .
   ```

2. **Type Hints**: Use type hints for function signatures
   ```python
   def process_file(input_path: Path, verbose: bool = False) -> bool:
       """Process a file and return success status"""
       pass
   ```

3. **Docstrings**: Use Google-style docstrings
   ```python
   def my_function(param1: str, param2: int) -> bool:
       """Short description of function.
       
       Longer description if needed.
       
       Args:
           param1: Description of param1
           param2: Description of param2
           
       Returns:
           Description of return value
       """
       pass
   ```

4. **Descriptive Names**: Use clear, descriptive variable and function names
   - Good: `extract_metadata_from_json`, `processed_file_count`
   - Bad: `get_data`, `cnt`, `x`

5. **Error Handling**: Include context in error messages and logging
   ```python
   try:
       process_file(path)
   except Exception as e:
       logger.error(f"Failed to process {path}: {e}")
       raise
   ```

6. **Logging**: Use the logging module, not print statements (except for user-facing output)
   ```python
   import logging
   logger = logging.getLogger(__name__)
   
   logger.debug(f"Processing {filename}")
   logger.info("Processing complete")
   logger.error(f"Failed to process: {error}")
   ```

### Project Structure

```
memoria/
├── memoria.py              # Main entry point
├── common/                 # Shared utilities
│   ├── utils.py
│   ├── dependency_checker.py
│   └── ...
├── processors/             # Processor modules
│   ├── base.py            # Abstract base class
│   ├── registry.py        # Auto-discovery system
│   └── platform_name/     # Individual processors
│       ├── processor.py   # Main processor logic
│       └── preprocess.py  # Optional preprocessing
└── standalone/            # Utility scripts
```

## Adding a New Processor

Follow these steps to add support for a new platform:

### 1. Create Processor Directory

```bash
mkdir processors/my_platform
cd processors/my_platform
```

### 2. Create `processor.py`

Use this template:

```python
#!/usr/bin/env python3
"""
My Platform Media Processor

Description of what this processor does.
"""
import logging
from pathlib import Path
from processors.base import ProcessorBase

logger = logging.getLogger(__name__)


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory
    
    Detection criteria:
    - Describe what you're checking for
    - List required files/directories
    
    Args:
        input_path: Path to the input directory
        
    Returns:
        True if this is a My Platform export, False otherwise
    """
    try:
        # Your detection logic here
        required_file = input_path / "my_platform_data.json"
        return required_file.exists()
        
    except Exception as e:
        logger.debug(f"Detection failed for My Platform: {e}")
        return False


class MyPlatformProcessor(ProcessorBase):
    """Processor for My Platform exports"""
    
    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)
    
    @staticmethod
    def get_name() -> str:
        return "My Platform"
    
    @staticmethod
    def get_priority() -> int:
        """Return priority (see processors/base.py for guidelines)
        
        - 80-100: Very specific detection (multiple required elements)
        - 50-79: Moderate specificity (directory structure)
        - 1-49: Broad detection (filename patterns)
        """
        return 50  # Adjust based on your detection specificity
    
    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process the export
        
        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional args (verbose, workers, temp_dir, etc.)
            
        Returns:
            True if processing succeeded, False otherwise
        """
        from common.processor_config import get_effective_output_dir
        
        verbose = kwargs.get("verbose", False)
        workers = kwargs.get("workers", 4)
        
        # Get processor-specific output directory
        effective_output = get_effective_output_dir("My Platform", output_dir)
        
        # Your processing logic here
        print(f"Processing My Platform export from: {input_dir}")
        print(f"Output directory: {effective_output}")
        
        # Return True on success, False on failure
        return True


def get_processor():
    """Return processor class for auto-discovery"""
    return MyPlatformProcessor
```

### 3. Test Your Processor

```bash
# Test detection
./memoria.py /path/to/test/export --verbose

# Verify it's discovered
./memoria.py --list-processors
```

### 4. Add Documentation

Update the main README.md to include:
- Platform name in "Supported Formats" section
- Any platform-specific requirements or notes

## Testing

### Manual Testing

1. **Test with sample data**: Use actual export data from the platform
2. **Test edge cases**: Empty exports, missing files, corrupted data
3. **Test error handling**: Invalid paths, missing dependencies
4. **Test verbose output**: Run with `--verbose` to check logging

### Running Tests

```bash
# Run all tests (when test suite is available)
pytest

# Run specific test file
pytest tests/test_my_module.py

# Run with coverage
pytest --cov=common --cov=processors
```

### Before Submitting

- [ ] Code follows Black formatting
- [ ] Type hints added to function signatures
- [ ] Docstrings added for all public functions/classes
- [ ] Error handling includes context
- [ ] Logging uses appropriate levels (DEBUG/INFO/ERROR)
- [ ] Tested with real export data
- [ ] No hardcoded paths or credentials
- [ ] Works with both `--verbose` and normal modes

## Submitting Changes

### Pull Request Process

1. **Fork the repository** and create a new branch from `main`:
   ```bash
   git checkout -b feature/my-new-processor
   ```

2. **Make your changes** following the code style guidelines

3. **Test thoroughly** with real export data

4. **Commit with descriptive messages**:
   ```bash
   git commit -m "Add processor for My Platform exports"
   ```
   
   Good commit messages:
   - "Add Instagram Reels processor"
   - "Fix metadata parsing for Google Photos albums"
   - "Improve error handling in exiftool batch operations"
   
   Bad commit messages:
   - "fix bug"
   - "update"
   - "changes"

5. **Push to your fork**:
   ```bash
   git push origin feature/my-new-processor
   ```

6. **Create a Pull Request** with:
   - Clear description of what changed
   - Why the change was needed
   - How to test the changes
   - Any related issues

### Pull Request Checklist

- [ ] Code follows project style guidelines
- [ ] All functions have docstrings
- [ ] Type hints are used appropriately
- [ ] Changes are tested with real data
- [ ] README updated if adding new features
- [ ] No secrets or personal data in commits
- [ ] Commits are logical and well-described

## Reporting Issues

### Bug Reports

When reporting bugs, include:

1. **Description**: What happened vs. what you expected
2. **Steps to Reproduce**: Exact commands and inputs used
3. **Environment**:
   - OS and version
   - Python version
   - Dependency versions (exiftool, ffmpeg)
4. **Logs**: Output with `--verbose` flag (redact any personal info)
5. **Sample Data**: If possible, provide minimal reproducible example

### Feature Requests

For feature requests, describe:

1. **Use Case**: What problem does this solve?
2. **Proposed Solution**: How should it work?
3. **Alternatives**: Other approaches you considered
4. **Examples**: Similar features in other tools

## Getting Help

- **Questions**: Open a GitHub Discussion or Issue
- **Documentation**: Check the [README](README.md) and [docs/](docs/) directory
- **Examples**: Look at existing processors for reference

## Code of Conduct

Be respectful, constructive, and professional in all interactions. We're all here to preserve digital memories and help each other.

---

Thank you for contributing to Memoria!

