"""ZIP utilities for file extraction, creation, and comparison."""
from __future__ import annotations
import zipfile
import tempfile
import difflib
import io
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Supported text file extensions
TEXT_EXTENSIONS = {
    '.py', '.ts', '.js', '.jsx', '.tsx', '.sql', '.json', '.yaml', '.yml', 
    '.md', '.txt', '.env', '.toml', '.ini', '.cfg', '.conf', '.html', '.htm',
    '.css', '.scss', '.sass', '.less', '.xml', '.svg', '.gitignore', '.dockerignore'
}

# Maximum file size for processing (5MB)
MAX_FILE_SIZE = 5 * 1024 * 1024

def is_text_file(file_path: str) -> bool:
    """Check if file should be processed as text based on extension."""
    path = Path(file_path)
    return path.suffix.lower() in TEXT_EXTENSIONS

def extract_text_files(zip_bytes: bytes, max_files: int = 1000) -> Dict[str, str]:
    """
    Extract text files from ZIP archive.
    
    Args:
        zip_bytes: ZIP file data
        max_files: Maximum number of files to process
        
    Returns:
        Dictionary mapping file paths to their text content
    """
    text_files = {}
    processed_count = 0
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_file:
            for file_info in zip_file.infolist():
                # Skip directories
                if file_info.is_dir():
                    continue
                
                # Check file count limit
                if processed_count >= max_files:
                    logger.warning(f"Reached maximum file limit ({max_files}), stopping extraction")
                    break
                
                file_path = file_info.filename
                
                # Skip if not a text file
                if not is_text_file(file_path):
                    continue
                
                # Check file size
                if file_info.file_size > MAX_FILE_SIZE:
                    logger.warning(f"File {file_path} too large ({file_info.file_size} bytes), skipping")
                    continue
                
                try:
                    # Extract and decode file content
                    with zip_file.open(file_info) as file:
                        content_bytes = file.read()
                        
                    # Try to decode as UTF-8
                    try:
                        content = content_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # Try other encodings
                        for encoding in ['latin-1', 'cp1252', 'utf-8-sig']:
                            try:
                                content = content_bytes.decode(encoding)
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            logger.warning(f"Could not decode {file_path}, skipping")
                            continue
                    
                    text_files[file_path] = content
                    processed_count += 1
                    
                except Exception as e:
                    logger.error(f"Error extracting {file_path}: {e}")
                    continue
                    
    except zipfile.BadZipFile as e:
        logger.error(f"Invalid ZIP file: {e}")
        raise ValueError("Invalid ZIP file format")
    except Exception as e:
        logger.error(f"Error processing ZIP file: {e}")
        raise
    
    logger.info(f"Extracted {len(text_files)} text files from ZIP archive")
    return text_files

def make_zip(files: Dict[str, str]) -> bytes:
    """
    Create ZIP archive from file dictionary.
    
    Args:
        files: Dictionary mapping file paths to their content
        
    Returns:
        ZIP file as bytes
    """
    zip_buffer = io.BytesIO()
    
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_path, content in files.items():
                # Ensure forward slashes in paths
                normalized_path = file_path.replace('\\', '/')
                
                # Write file to ZIP
                zip_file.writestr(normalized_path, content.encode('utf-8'))
                
        zip_buffer.seek(0)
        zip_bytes = zip_buffer.getvalue()
        
        logger.info(f"Created ZIP archive with {len(files)} files ({len(zip_bytes)} bytes)")
        return zip_bytes
        
    except Exception as e:
        logger.error(f"Error creating ZIP file: {e}")
        raise

def diff_archives(zip1_bytes: bytes, zip2_bytes: bytes) -> Tuple[str, Dict[str, str]]:
    """
    Compare two ZIP archives and generate unified diff.
    
    Args:
        zip1_bytes: Original ZIP archive
        zip2_bytes: New ZIP archive
        
    Returns:
        Tuple of (summary, full_diff_dict)
        - summary: Human-readable summary of changes
        - full_diff_dict: Dictionary with detailed diffs per file
    """
    try:
        # Extract files from both archives
        files1 = extract_text_files(zip1_bytes)
        files2 = extract_text_files(zip2_bytes)
        
        # Track changes
        added_files = []
        removed_files = []
        modified_files = []
        unchanged_files = []
        diff_details = {}
        
        # Find all unique file paths
        all_files = set(files1.keys()) | set(files2.keys())
        
        for file_path in sorted(all_files):
            content1 = files1.get(file_path, "")
            content2 = files2.get(file_path, "")
            
            if file_path not in files1:
                # New file
                added_files.append(file_path)
                diff_details[file_path] = f"+ NEW FILE: {file_path}\n" + "\n".join(f"+ {line}" for line in content2.split('\n'))
                
            elif file_path not in files2:
                # Deleted file
                removed_files.append(file_path)
                diff_details[file_path] = f"- DELETED FILE: {file_path}\n" + "\n".join(f"- {line}" for line in content1.split('\n'))
                
            elif content1 != content2:
                # Modified file
                modified_files.append(file_path)
                
                # Generate unified diff
                lines1 = content1.splitlines(keepends=True)
                lines2 = content2.splitlines(keepends=True)
                
                diff = list(difflib.unified_diff(
                    lines1, lines2,
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                    lineterm=""
                ))
                
                diff_details[file_path] = "".join(diff)
                
            else:
                # Unchanged file
                unchanged_files.append(file_path)
        
        # Generate summary
        summary_lines = [
            f"📊 Archive Comparison Summary",
            f"",
            f"📁 Files processed: {len(all_files)}",
            f"✅ Unchanged: {len(unchanged_files)}",
            f"📝 Modified: {len(modified_files)}",
            f"➕ Added: {len(added_files)}",
            f"❌ Removed: {len(removed_files)}",
            f""
        ]
        
        if added_files:
            summary_lines.append("➕ Added files:")
            for file_path in added_files[:10]:  # Show first 10
                summary_lines.append(f"  + {file_path}")
            if len(added_files) > 10:
                summary_lines.append(f"  ... and {len(added_files) - 10} more")
            summary_lines.append("")
        
        if removed_files:
            summary_lines.append("❌ Removed files:")
            for file_path in removed_files[:10]:
                summary_lines.append(f"  - {file_path}")
            if len(removed_files) > 10:
                summary_lines.append(f"  ... and {len(removed_files) - 10} more")
            summary_lines.append("")
        
        if modified_files:
            summary_lines.append("📝 Modified files:")
            for file_path in modified_files[:10]:
                summary_lines.append(f"  ~ {file_path}")
            if len(modified_files) > 10:
                summary_lines.append(f"  ... and {len(modified_files) - 10} more")
        
        summary = "\n".join(summary_lines)
        
        logger.info(f"Generated diff: {len(modified_files)} modified, {len(added_files)} added, {len(removed_files)} removed")
        
        return summary, diff_details
        
    except Exception as e:
        logger.error(f"Error comparing archives: {e}")
        raise

def validate_zip_file(zip_bytes: bytes, max_size: int = 20 * 1024 * 1024) -> Tuple[bool, str]:
    """
    Validate ZIP file for processing.
    
    Args:
        zip_bytes: ZIP file data
        max_size: Maximum allowed size in bytes
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(zip_bytes) > max_size:
        return False, f"ZIP file too large ({len(zip_bytes)} bytes, max {max_size})"
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_file:
            # Test ZIP integrity
            bad_files = zip_file.testzip()
            if bad_files:
                return False, f"Corrupted files in ZIP: {bad_files}"
                
            # Count text files
            text_file_count = 0
            for file_info in zip_file.infolist():
                if not file_info.is_dir() and is_text_file(file_info.filename):
                    text_file_count += 1
            
            if text_file_count == 0:
                return False, "No processable text files found in ZIP"
                
        return True, ""
        
    except zipfile.BadZipFile:
        return False, "Invalid ZIP file format"
    except Exception as e:
        return False, f"Error validating ZIP: {e}"

def get_file_stats(zip_bytes: bytes) -> Dict[str, Any]:
    """
    Get statistics about ZIP file contents.
    
    Args:
        zip_bytes: ZIP file data
        
    Returns:
        Dictionary with file statistics
    """
    stats = {
        'total_files': 0,
        'text_files': 0,
        'binary_files': 0,
        'total_size': len(zip_bytes),
        'extensions': {},
        'largest_file': {'name': '', 'size': 0}
    }
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_file:
            for file_info in zip_file.infolist():
                if file_info.is_dir():
                    continue
                    
                stats['total_files'] += 1
                
                # Track extension
                ext = Path(file_info.filename).suffix.lower()
                stats['extensions'][ext] = stats['extensions'].get(ext, 0) + 1
                
                # Track largest file
                if file_info.file_size > stats['largest_file']['size']:
                    stats['largest_file'] = {
                        'name': file_info.filename,
                        'size': file_info.file_size
                    }
                
                # Count text vs binary
                if is_text_file(file_info.filename):
                    stats['text_files'] += 1
                else:
                    stats['binary_files'] += 1
                    
    except Exception as e:
        logger.error(f"Error getting ZIP stats: {e}")
    
    return stats