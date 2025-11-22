#!/usr/bin/env python3
"""
Export Comparison Tool
Compares two processed media exports and logs all differences including:
- Directory structure
- File lists and filenames
- File contents (via hash comparison)
- EXIF/XMP metadata
- File sizes and timestamps
"""

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any, Optional
from collections import defaultdict

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    print("Warning: tqdm not available, progress bars disabled")


class ExportComparator:
    """Compares two processed media exports"""
    
    def __init__(self, dir1: Path, dir2: Path, log_file: Path, 
                 skip_content: bool = False, skip_metadata: bool = False,
                 ignore_patterns: List[str] = None):
        self.dir1 = dir1.resolve()
        self.dir2 = dir2.resolve()
        self.log_file = log_file
        self.skip_content = skip_content
        self.skip_metadata = skip_metadata
        self.ignore_patterns = ignore_patterns or []
        
        # Results storage
        self.differences = defaultdict(list)
        self.stats = {
            'total_files_dir1': 0,
            'total_files_dir2': 0,
            'matched_files': 0,
            'files_only_in_dir1': 0,
            'files_only_in_dir2': 0,
            'content_mismatches': 0,
            'metadata_mismatches': 0,
            'size_mismatches': 0,
        }
        
        # Set up logging
        self.logger = logging.getLogger(__name__)
        self._setup_logging()
    
    def _setup_logging(self):
        """Configure logging to both file and console"""
        # Clear any existing handlers
        self.logger.handlers.clear()
        self.logger.setLevel(logging.DEBUG)
        
        # File handler
        fh = logging.FileHandler(self.log_file, mode='w', encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        fh.setFormatter(file_formatter)
        self.logger.addHandler(fh)
        
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s - %(message)s')
        ch.setFormatter(console_formatter)
        self.logger.addHandler(ch)
    
    def _should_ignore(self, path: Path) -> bool:
        """Check if path matches any ignore patterns"""
        path_str = str(path)
        for pattern in self.ignore_patterns:
            if pattern in path_str:
                return True
        return False
    
    def _get_all_files(self, directory: Path) -> Dict[str, Path]:
        """
        Get all files in directory recursively.
        Returns dict mapping relative path to absolute path.
        """
        files = {}
        for root, dirs, filenames in os.walk(directory):
            root_path = Path(root)
            for filename in filenames:
                full_path = root_path / filename
                
                if self._should_ignore(full_path):
                    continue
                
                try:
                    rel_path = full_path.relative_to(directory)
                    files[str(rel_path)] = full_path
                except ValueError:
                    self.logger.warning(f"Could not get relative path for {full_path}")
                    continue
        
        return files
    
    def _get_all_dirs(self, directory: Path) -> Set[str]:
        """Get all subdirectories recursively as relative paths"""
        dirs = set()
        for root, dirnames, _ in os.walk(directory):
            root_path = Path(root)
            for dirname in dirnames:
                full_path = root_path / dirname
                
                if self._should_ignore(full_path):
                    continue
                
                try:
                    rel_path = full_path.relative_to(directory)
                    dirs.add(str(rel_path))
                except ValueError:
                    continue
        
        return dirs
    
    def _compute_file_hash(self, filepath: Path, algorithm: str = 'sha256') -> str:
        """Compute hash of file contents"""
        hash_func = hashlib.new(algorithm)
        
        try:
            with open(filepath, 'rb') as f:
                # Read in chunks for memory efficiency
                for chunk in iter(lambda: f.read(8192), b''):
                    hash_func.update(chunk)
            return hash_func.hexdigest()
        except Exception as e:
            self.logger.error(f"Error computing hash for {filepath}: {e}")
            return None
    
    def _batch_get_exif_metadata(self, filepaths: List[Path]) -> Dict[str, Dict[str, Any]]:
        """Extract EXIF/XMP metadata for multiple files using batch exiftool call
        
        Args:
            filepaths: List of file paths to process
            
        Returns:
            Dict mapping file path (string) to metadata dict
        """
        if not filepaths:
            return {}
        
        metadata_map = {}
        
        # Process in chunks to avoid command line length limits
        chunk_size = 500
        for i in range(0, len(filepaths), chunk_size):
            chunk = filepaths[i:i + chunk_size]
            
            try:
                cmd = ['exiftool', '-json', '-G', '-a']
                cmd.extend([str(fp) for fp in chunk])
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout for batch
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
                
                if result.returncode == 0 and result.stdout:
                    metadata_list = json.loads(result.stdout)
                    for item in metadata_list:
                        source_file = item.get('SourceFile') or item.get('File:FileName')
                        if source_file:
                            # Normalize path to match input
                            metadata_map[str(Path(source_file).resolve())] = item
                
            except FileNotFoundError:
                self.logger.warning("exiftool not found - skipping metadata comparison")
                return {}
            except subprocess.TimeoutExpired:
                self.logger.warning(f"exiftool timeout for chunk starting at index {i}")
                continue
            except json.JSONDecodeError as e:
                self.logger.warning(f"Failed to parse exiftool JSON output: {e}")
                continue
            except Exception as e:
                self.logger.warning(f"Error batch reading metadata: {e}")
                continue
        
        return metadata_map
    
    def _get_exif_metadata(self, filepath: Path) -> Optional[Dict[str, Any]]:
        """Extract EXIF/XMP metadata using exiftool (single file fallback)"""
        try:
            result = subprocess.run(
                ['exiftool', '-json', '-G', '-a', str(filepath)],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            
            if result.returncode == 0 and result.stdout:
                metadata_list = json.loads(result.stdout)
                if metadata_list:
                    return metadata_list[0]
            
            return None
        except FileNotFoundError:
            self.logger.warning("exiftool not found - skipping metadata comparison")
            return None
        except subprocess.TimeoutExpired:
            self.logger.warning(f"exiftool timeout for {filepath}")
            return None
        except Exception as e:
            self.logger.debug(f"Error reading metadata for {filepath}: {e}")
            return None
    
    def _compare_metadata_pair(self, meta1: Optional[Dict[str, Any]], 
                               meta2: Optional[Dict[str, Any]], 
                               rel_path: str) -> bool:
        """Compare metadata of two files. Returns True if identical."""
        if meta1 is None and meta2 is None:
            return True  # Both have no metadata
        
        if meta1 is None or meta2 is None:
            self.differences['metadata'].append({
                'file': rel_path,
                'issue': 'One file has metadata, the other does not',
                'dir1_has_metadata': meta1 is not None,
                'dir2_has_metadata': meta2 is not None
            })
            return False
        
        # Ignore certain fields that may legitimately differ
        ignore_fields = {
            'SourceFile', 'File:FileModifyDate', 'File:FileAccessDate',
            'File:FileInodeChangeDate', 'File:Directory', 'File:FileName',
            'System:FileModifyDate', 'System:FileAccessDate',
            'System:FileInodeChangeDate', 'System:Directory', 'System:FileName'
        }
        
        # Get all unique keys
        all_keys = set(meta1.keys()) | set(meta2.keys())
        all_keys -= ignore_fields
        
        differences = []
        for key in sorted(all_keys):
            val1 = meta1.get(key)
            val2 = meta2.get(key)
            
            if val1 != val2:
                differences.append({
                    'field': key,
                    'dir1_value': val1,
                    'dir2_value': val2
                })
        
        if differences:
            self.differences['metadata'].append({
                'file': rel_path,
                'differences': differences
            })
            return False
        
        return True
    
    def compare_directory_structure(self):
        """Compare directory structures between the two exports"""
        self.logger.info("Comparing directory structures...")
        
        dirs1 = self._get_all_dirs(self.dir1)
        dirs2 = self._get_all_dirs(self.dir2)
        
        self.logger.debug(f"Directory 1 has {len(dirs1)} subdirectories")
        self.logger.debug(f"Directory 2 has {len(dirs2)} subdirectories")
        
        only_in_dir1 = dirs1 - dirs2
        only_in_dir2 = dirs2 - dirs1
        common_dirs = dirs1 & dirs2
        
        if only_in_dir1:
            self.logger.warning(f"Found {len(only_in_dir1)} directories only in dir1")
            self.differences['dirs_only_in_dir1'] = sorted(only_in_dir1)
        
        if only_in_dir2:
            self.logger.warning(f"Found {len(only_in_dir2)} directories only in dir2")
            self.differences['dirs_only_in_dir2'] = sorted(only_in_dir2)
        
        if not only_in_dir1 and not only_in_dir2:
            self.logger.info(f"Directory structures match ({len(common_dirs)} common directories)")
        
        return len(only_in_dir1) == 0 and len(only_in_dir2) == 0
    
    def compare_file_lists(self):
        """Compare file lists and filenames between the two exports"""
        self.logger.info("Building file lists...")
        
        files1 = self._get_all_files(self.dir1)
        files2 = self._get_all_files(self.dir2)
        
        self.stats['total_files_dir1'] = len(files1)
        self.stats['total_files_dir2'] = len(files2)
        
        self.logger.info(f"Directory 1: {len(files1)} files")
        self.logger.info(f"Directory 2: {len(files2)} files")
        
        files1_set = set(files1.keys())
        files2_set = set(files2.keys())
        
        only_in_dir1 = files1_set - files2_set
        only_in_dir2 = files2_set - files1_set
        common_files = files1_set & files2_set
        
        self.stats['files_only_in_dir1'] = len(only_in_dir1)
        self.stats['files_only_in_dir2'] = len(only_in_dir2)
        self.stats['matched_files'] = len(common_files)
        
        if only_in_dir1:
            self.logger.warning(f"Found {len(only_in_dir1)} files only in dir1")
            self.differences['files_only_in_dir1'] = sorted(only_in_dir1)
        
        if only_in_dir2:
            self.logger.warning(f"Found {len(only_in_dir2)} files only in dir2")
            self.differences['files_only_in_dir2'] = sorted(only_in_dir2)
        
        if not only_in_dir1 and not only_in_dir2:
            self.logger.info(f"File lists match ({len(common_files)} common files)")
        
        return common_files, files1, files2
    
    def compare_file_contents(self, common_files: Set[str], files1: Dict[str, Path], 
                             files2: Dict[str, Path]):
        """Compare contents of matching files using hash comparison"""
        if self.skip_content:
            self.logger.info("Skipping content comparison (--skip-content)")
            return
        
        self.logger.info(f"Comparing contents of {len(common_files)} common files...")
        
        iterator = tqdm(sorted(common_files), desc="Comparing files") if HAS_TQDM else sorted(common_files)
        
        for rel_path in iterator:
            file1 = files1[rel_path]
            file2 = files2[rel_path]
            
            # Compare file sizes first (quick check)
            size1 = file1.stat().st_size
            size2 = file2.stat().st_size
            
            if size1 != size2:
                self.stats['size_mismatches'] += 1
                self.differences['size_mismatch'].append({
                    'file': rel_path,
                    'dir1_size': size1,
                    'dir2_size': size2,
                    'difference': abs(size1 - size2)
                })
                self.logger.debug(f"Size mismatch: {rel_path}")
                continue
            
            # Compare content hashes
            hash1 = self._compute_file_hash(file1)
            hash2 = self._compute_file_hash(file2)
            
            if hash1 is None or hash2 is None:
                self.logger.warning(f"Could not compute hash for {rel_path}")
                continue
            
            if hash1 != hash2:
                self.stats['content_mismatches'] += 1
                self.differences['content_mismatch'].append({
                    'file': rel_path,
                    'dir1_hash': hash1,
                    'dir2_hash': hash2
                })
                self.logger.debug(f"Content mismatch: {rel_path}")
        
        if self.stats['content_mismatches'] == 0 and self.stats['size_mismatches'] == 0:
            self.logger.info("All file contents match!")
        else:
            if self.stats['size_mismatches'] > 0:
                self.logger.warning(f"Found {self.stats['size_mismatches']} files with size mismatches")
            if self.stats['content_mismatches'] > 0:
                self.logger.warning(f"Found {self.stats['content_mismatches']} files with content mismatches")
    
    def compare_metadata(self, common_files: Set[str], files1: Dict[str, Path], 
                        files2: Dict[str, Path]):
        """Compare EXIF/XMP metadata of matching files using batch processing"""
        if self.skip_metadata:
            self.logger.info("Skipping metadata comparison (--skip-metadata)")
            return
        
        self.logger.info(f"Comparing metadata of {len(common_files)} common files...")
        
        # Check if exiftool is available
        try:
            subprocess.run(['exiftool', '-ver'], capture_output=True, check=True, stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.logger.warning("exiftool not available - skipping metadata comparison")
            return
        
        # Filter to only media files
        media_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.mp4', '.mov', '.avi', 
                           '.heic', '.heif', '.webp', '.tiff', '.tif', '.3gp', '.mkv'}
        
        media_files = []
        for rel_path in sorted(common_files):
            file1 = files1[rel_path]
            ext = file1.suffix.lower()
            if ext in media_extensions:
                media_files.append((rel_path, file1, files2[rel_path]))
        
        if not media_files:
            self.logger.info("No media files to compare metadata")
            return
        
        self.logger.info(f"Reading metadata for {len(media_files)} media files using batch processing...")
        
        # Batch read metadata from both directories
        files1_paths = [item[1] for item in media_files]
        files2_paths = [item[2] for item in media_files]
        
        self.logger.debug("Reading metadata from directory 1...")
        metadata1_map = self._batch_get_exif_metadata(files1_paths)
        
        self.logger.debug("Reading metadata from directory 2...")
        metadata2_map = self._batch_get_exif_metadata(files2_paths)
        
        # Compare metadata for each file
        self.logger.info("Comparing metadata...")
        iterator = tqdm(media_files, desc="Comparing metadata") if HAS_TQDM else media_files
        
        for rel_path, file1, file2 in iterator:
            # Get metadata from batch results
            file1_str = str(file1.resolve())
            file2_str = str(file2.resolve())
            
            meta1 = metadata1_map.get(file1_str)
            meta2 = metadata2_map.get(file2_str)
            
            if not self._compare_metadata_pair(meta1, meta2, rel_path):
                self.stats['metadata_mismatches'] += 1
        
        if self.stats['metadata_mismatches'] == 0:
            self.logger.info("All metadata matches!")
        else:
            self.logger.warning(f"Found {self.stats['metadata_mismatches']} files with metadata mismatches")
    
    def generate_report(self):
        """Generate final comparison report"""
        self.logger.info("\n" + "="*80)
        self.logger.info("COMPARISON SUMMARY")
        self.logger.info("="*80)
        
        self.logger.info(f"\nDirectories compared:")
        self.logger.info(f"  Dir1: {self.dir1}")
        self.logger.info(f"  Dir2: {self.dir2}")
        
        self.logger.info(f"\nFile counts:")
        self.logger.info(f"  Total files in dir1: {self.stats['total_files_dir1']}")
        self.logger.info(f"  Total files in dir2: {self.stats['total_files_dir2']}")
        self.logger.info(f"  Matched files: {self.stats['matched_files']}")
        self.logger.info(f"  Files only in dir1: {self.stats['files_only_in_dir1']}")
        self.logger.info(f"  Files only in dir2: {self.stats['files_only_in_dir2']}")
        
        self.logger.info(f"\nDifferences found:")
        self.logger.info(f"  Size mismatches: {self.stats['size_mismatches']}")
        self.logger.info(f"  Content mismatches: {self.stats['content_mismatches']}")
        self.logger.info(f"  Metadata mismatches: {self.stats['metadata_mismatches']}")
        self.logger.info(f"  Directories only in dir1: {len(self.differences.get('dirs_only_in_dir1', []))}")
        self.logger.info(f"  Directories only in dir2: {len(self.differences.get('dirs_only_in_dir2', []))}")
        
        # Detailed differences
        if self.differences:
            self.logger.info("\n" + "="*80)
            self.logger.info("DETAILED DIFFERENCES")
            self.logger.info("="*80)
            
            if self.differences.get('dirs_only_in_dir1'):
                self.logger.info("\nDirectories only in dir1:")
                for d in self.differences['dirs_only_in_dir1'][:20]:  # Limit output
                    self.logger.info(f"  - {d}")
                if len(self.differences['dirs_only_in_dir1']) > 20:
                    self.logger.info(f"  ... and {len(self.differences['dirs_only_in_dir1']) - 20} more")
            
            if self.differences.get('dirs_only_in_dir2'):
                self.logger.info("\nDirectories only in dir2:")
                for d in self.differences['dirs_only_in_dir2'][:20]:
                    self.logger.info(f"  - {d}")
                if len(self.differences['dirs_only_in_dir2']) > 20:
                    self.logger.info(f"  ... and {len(self.differences['dirs_only_in_dir2']) - 20} more")
            
            if self.differences.get('files_only_in_dir1'):
                self.logger.info("\nFiles only in dir1:")
                for f in self.differences['files_only_in_dir1'][:20]:
                    self.logger.info(f"  - {f}")
                if len(self.differences['files_only_in_dir1']) > 20:
                    self.logger.info(f"  ... and {len(self.differences['files_only_in_dir1']) - 20} more")
            
            if self.differences.get('files_only_in_dir2'):
                self.logger.info("\nFiles only in dir2:")
                for f in self.differences['files_only_in_dir2'][:20]:
                    self.logger.info(f"  - {f}")
                if len(self.differences['files_only_in_dir2']) > 20:
                    self.logger.info(f"  ... and {len(self.differences['files_only_in_dir2']) - 20} more")
            
            if self.differences.get('size_mismatch'):
                self.logger.info("\nFile size mismatches:")
                for item in self.differences['size_mismatch'][:10]:
                    self.logger.info(f"  - {item['file']}")
                    self.logger.info(f"    Dir1: {item['dir1_size']:,} bytes")
                    self.logger.info(f"    Dir2: {item['dir2_size']:,} bytes")
                if len(self.differences['size_mismatch']) > 10:
                    self.logger.info(f"  ... and {len(self.differences['size_mismatch']) - 10} more")
            
            if self.differences.get('content_mismatch'):
                self.logger.info("\nFile content mismatches:")
                for item in self.differences['content_mismatch'][:10]:
                    self.logger.info(f"  - {item['file']}")
                if len(self.differences['content_mismatch']) > 10:
                    self.logger.info(f"  ... and {len(self.differences['content_mismatch']) - 10} more")
            
            if self.differences.get('metadata'):
                self.logger.info("\nMetadata mismatches:")
                for item in self.differences['metadata'][:5]:
                    self.logger.info(f"  - {item['file']}")
                    if 'differences' in item:
                        for diff in item['differences'][:3]:
                            self.logger.info(f"    {diff['field']}:")
                            self.logger.info(f"      Dir1: {diff['dir1_value']}")
                            self.logger.info(f"      Dir2: {diff['dir2_value']}")
                        if len(item['differences']) > 3:
                            self.logger.info(f"    ... and {len(item['differences']) - 3} more fields")
                if len(self.differences['metadata']) > 5:
                    self.logger.info(f"  ... and {len(self.differences['metadata']) - 5} more files")
        
        # Final verdict
        total_issues = (
            self.stats['files_only_in_dir1'] +
            self.stats['files_only_in_dir2'] +
            self.stats['size_mismatches'] +
            self.stats['content_mismatches'] +
            self.stats['metadata_mismatches'] +
            len(self.differences.get('dirs_only_in_dir1', [])) +
            len(self.differences.get('dirs_only_in_dir2', []))
        )
        
        self.logger.info("\n" + "="*80)
        if total_issues == 0:
            self.logger.info("RESULT: Exports are IDENTICAL")
        else:
            self.logger.info(f"RESULT: Found {total_issues} differences between exports")
        self.logger.info("="*80)
        
        # Save detailed JSON report
        json_report_path = self.log_file.with_suffix('.json')
        report_data = {
            'timestamp': datetime.now().isoformat(),
            'dir1': str(self.dir1),
            'dir2': str(self.dir2),
            'stats': self.stats,
            'differences': dict(self.differences),
            'identical': total_issues == 0
        }
        
        with open(json_report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"\nDetailed JSON report saved to: {json_report_path}")
        
        return total_issues == 0
    
    def run(self) -> bool:
        """Run the full comparison. Returns True if exports are identical."""
        self.logger.info("Starting export comparison...")
        self.logger.info(f"Dir1: {self.dir1}")
        self.logger.info(f"Dir2: {self.dir2}")
        self.logger.info(f"Log file: {self.log_file}")
        
        # Verify directories exist
        if not self.dir1.exists():
            self.logger.error(f"Directory 1 does not exist: {self.dir1}")
            return False
        
        if not self.dir2.exists():
            self.logger.error(f"Directory 2 does not exist: {self.dir2}")
            return False
        
        # Run comparisons
        self.compare_directory_structure()
        common_files, files1, files2 = self.compare_file_lists()
        self.compare_file_contents(common_files, files1, files2)
        self.compare_metadata(common_files, files1, files2)
        
        # Generate report
        return self.generate_report()


def main():
    parser = argparse.ArgumentParser(
        description='Compare two processed media exports and log differences',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic comparison
  python compare_exports.py /path/to/export1 /path/to/export2
  
  # Skip content comparison (faster, only checks filenames and structure)
  python compare_exports.py export1/ export2/ --skip-content
  
  # Skip metadata comparison
  python compare_exports.py export1/ export2/ --skip-metadata
  
  # Ignore certain patterns
  python compare_exports.py export1/ export2/ --ignore ".DS_Store" --ignore "__pycache__"
  
  # Custom log file location
  python compare_exports.py export1/ export2/ --log comparison_results.log
        """
    )
    
    parser.add_argument('dir1', type=Path,
                       help='First export directory')
    parser.add_argument('dir2', type=Path,
                       help='Second export directory')
    
    # Create logs directory for default log file
    logs_dir = Path('logs')
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    parser.add_argument('--log', type=Path, 
                       default=logs_dir / f'export_comparison_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
                       help='Log file path (default: logs/export_comparison_TIMESTAMP.log)')
    parser.add_argument('--skip-content', action='store_true',
                       help='Skip file content comparison (only check structure and filenames)')
    parser.add_argument('--skip-metadata', action='store_true',
                       help='Skip EXIF/XMP metadata comparison')
    parser.add_argument('--ignore', action='append', dest='ignore_patterns',
                       help='Patterns to ignore (can be specified multiple times)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Enable verbose debug output')
    
    args = parser.parse_args()
    
    # Create comparator and run
    comparator = ExportComparator(
        dir1=args.dir1,
        dir2=args.dir2,
        log_file=args.log,
        skip_content=args.skip_content,
        skip_metadata=args.skip_metadata,
        ignore_patterns=args.ignore_patterns
    )
    
    # Adjust logging level if verbose
    if args.verbose:
        comparator.logger.setLevel(logging.DEBUG)
        for handler in comparator.logger.handlers:
            handler.setLevel(logging.DEBUG)
    
    identical = comparator.run()
    
    sys.exit(0 if identical else 1)


if __name__ == '__main__':
    main()

