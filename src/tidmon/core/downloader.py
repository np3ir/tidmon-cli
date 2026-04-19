"""
Advanced download system for tidemon
Based on tiddl with improvements for robustness and reliability

Features:
- Automatic retry with exponential backoff
- File integrity verification
- Corrupt file detection and repair
- Priority queue system
- Concurrent downloads with thread control
- Rate limiting handling
- Automatic quality fallback
"""

import asyncio
import shutil
import hashlib
import uuid
import logging
from pathlib import Path
from typing import Optional, Callable, Literal
from dataclasses import dataclass
from enum import Enum

import aiofiles
import aiohttp

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024**2  # 1MB
MAX_RETRIES = 3


# ====================================================================
# ENUMS Y DATACLASSES
# ====================================================================

class DownloadStatus(Enum):
    """Possible download states"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CORRUPTED = "corrupted"


class DownloadPriority(Enum):
    """Download priorities"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class DownloadTask:
    """Download task with full metadata"""
    url: str
    output_path: Path
    track_id: Optional[int] = None
    track_title: Optional[str] = None
    expected_size: Optional[int] = None
    expected_hash: Optional[str] = None
    status: DownloadStatus = DownloadStatus.PENDING
    priority: DownloadPriority = DownloadPriority.NORMAL
    attempts: int = 0
    max_attempts: int = 3
    bytes_downloaded: int = 0
    error_message: Optional[str] = None
    
    @property
    def progress_percentage(self) -> float:
        """Progress percentage (0-100)"""
        if not self.expected_size or self.expected_size == 0:
            return 0.0
        return (self.bytes_downloaded / self.expected_size) * 100
    
    @property
    def can_retry(self) -> bool:
        """Check if retry is possible"""
        return self.attempts < self.max_attempts
    
    def increment_attempt(self) -> None:
        """Increment attempt counter"""
        self.attempts += 1


# ====================================================================
# INTEGRITY CHECKER
# ====================================================================

class FileIntegrityChecker:
    """Downloaded file integrity checker"""
    
    @staticmethod
    async def verify_file_async(
        file_path: Path,
        expected_size: Optional[int] = None,
        expected_hash: Optional[str] = None,
        hash_algorithm: Literal["md5", "sha256"] = "md5"
    ) -> tuple[bool, Optional[str]]:
        """
        Verifies the integrity of a file
        
        Returns:
            tuple[is_valid, error_message]
        """
        if not file_path.exists():
            return False, "File does not exist"
        
        # Check size
        actual_size = file_path.stat().st_size
        
        # Very small files are suspicious (possible HTML errors)
        if actual_size < 2048:  # Less than 2KB
            return False, f"File too small ({actual_size} bytes)"
        
        # Check expected size (with 1KB tolerance)
        if expected_size and abs(actual_size - expected_size) > 1024:
            return False, f"Size mismatch: expected {expected_size}, got {actual_size}"
        
        # Check magic bytes according to extension
        try:
            async with aiofiles.open(file_path, "rb") as f:
                header = await f.read(12)
                
                if not FileIntegrityChecker._check_magic_bytes(file_path, header):
                    return False, "Invalid file format (magic bytes check failed)"
                
                # For MP4/M4A files, check atoms
                if file_path.suffix.lower() in ['.m4a', '.mp4', '.m4v']:
                    await f.seek(0)
                    first_256kb = await f.read(262144)
                    
                    if b'moov' not in first_256kb:
                        return False, "Invalid MP4/M4A: missing 'moov' atom"
                
                # Check hash if provided
                if expected_hash:
                    actual_hash = await FileIntegrityChecker._calculate_hash_async(
                        file_path,
                        hash_algorithm
                    )
                    
                    if actual_hash != expected_hash.lower():
                        return False, f"Hash mismatch"
        
        except Exception as e:
            return False, f"Verification error: {str(e)}"
        
        return True, None
    
    @staticmethod
    def _check_magic_bytes(file_path: Path, header: bytes) -> bool:
        """Verifies magic bytes according to file type"""
        ext = file_path.suffix.lower()
        
        # FLAC
        if ext == '.flac':
            return header.startswith(b'fLaC')
        
        # MP4/M4A
        elif ext in ['.m4a', '.mp4', '.m4v']:
            return len(header) >= 8 and header[4:8] == b'ftyp'
        
        # MP3
        elif ext == '.mp3':
            # ID3v2 tag or frame sync
            return header.startswith(b'ID3') or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)
        
        # AAC
        elif ext == '.aac':
            return header.startswith(b'\xFF\xF1') or header.startswith(b'\xFF\xF9')
        
        # If we don't know the format, assume valid
        return True
    
    @staticmethod
    async def _calculate_hash_async(
        file_path: Path,
        algorithm: Literal["md5", "sha256"] = "md5"
    ) -> str:
        """Calculates the hash of a file"""
        hash_obj = hashlib.md5() if algorithm == "md5" else hashlib.sha256()
        
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(8192)
                if not chunk:
                    break
                hash_obj.update(chunk)
        
        return hash_obj.hexdigest()


# ====================================================================
# ADVANCED DOWNLOADER
# ====================================================================

class AdvancedDownloader:
    """
    Advanced downloader with professional features
    """
    
    def __init__(
        self,
        max_concurrent: int = 3,
        chunk_size: int = 1024**2,
        timeout: int = 300,
        on_progress: Optional[Callable] = None,
    ):
        self.max_concurrent = max_concurrent
        self.chunk_size = chunk_size
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.on_progress = on_progress
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Statistics
        self.stats = {
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "corrupted": 0,
            "total_bytes": 0,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=self.max_concurrent)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self):
        """Close the aiohttp session if it exists."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Downloader aiohttp session closed.")
    
    async def download_file(
        self,
        task: DownloadTask,
        session: aiohttp.ClientSession,
        on_chunk: Optional[Callable] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Downloads a file with retry and verification
        
        Returns:
            tuple[success, error_message]
        """
        async with self.semaphore:
            # If the file exists and is valid, skip
            if task.output_path.exists():
                is_valid, error = await FileIntegrityChecker.verify_file_async(
                    task.output_path,
                    task.expected_size
                )
                
                if is_valid:
                    task.status = DownloadStatus.SKIPPED
                    self.stats["skipped"] += 1
                    logger.info(f"[OK] Skipped (exists): {task.track_title}")
                    if self.on_progress: self.on_progress(task)
                    return True, None
                else:
                    # Corrupt file, delete and retry
                    logger.warning(f"Existing file is corrupt, redownloading: {task.track_title}")
                    task.output_path.unlink()
            
            # Attempt download with retries
            tmp_path = None
            
            while task.can_retry:
                task.increment_attempt()
                task.status = DownloadStatus.DOWNLOADING
                
                try:
                    # Create temporary file with unique name
                    unique_suffix = f".part.{uuid.uuid4().hex[:8]}"
                    tmp_path = task.output_path.with_suffix(
                        task.output_path.suffix + unique_suffix
                    )
                    
                    # Download
                    success, error = await self._download_with_progress(
                        task, session, tmp_path, on_chunk
                    )
                    
                    if not success:
                        # Handle rate limiting
                        if "429" in str(error) or "rate limit" in str(error).lower():
                            wait_time = min(60 * task.attempts, 300)  # Max 5 min
                            logger.warning(f"Rate limited, waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        
                        # Other error - exponential backoff
                        await asyncio.sleep(2 ** task.attempts)
                        continue
                    
                    # Move temporary file to destination
                    move_success = False
                    for move_attempt in range(5):
                        try:
                            shutil.move(str(tmp_path), str(task.output_path))
                            move_success = True
                            break
                        except OSError as e:
                            if move_attempt == 4:
                                raise e
                            logger.warning(f"File move locked, retrying... (attempt {move_attempt + 1})")
                            await asyncio.sleep(1.0 + move_attempt)
                    
                    tmp_path = None  # No longer exists
                    
                    # Verify integrity
                    task.status = DownloadStatus.VERIFYING
                    is_valid, error_msg = await FileIntegrityChecker.verify_file_async(
                        task.output_path,
                        expected_size=task.expected_size,
                        expected_hash=task.expected_hash
                    )
                    
                    if is_valid:
                        task.status = DownloadStatus.COMPLETED
                        self.stats["completed"] += 1
                        self.stats["total_bytes"] += task.bytes_downloaded
                        
                        if task.attempts > 1:
                            logger.info(f"[OK] Downloaded (attempt {task.attempts}): {task.track_title}")
                        
                        if self.on_progress: self.on_progress(task)
                        return True, None
                    else:
                        # Corrupt file
                        task.status = DownloadStatus.CORRUPTED
                        self.stats["corrupted"] += 1
                        logger.warning(f"File corrupted: {task.track_title} - {error_msg}")
                        
                        # Delete corrupt file
                        if task.output_path.exists():
                            task.output_path.unlink()
                        
                        if not task.can_retry:
                            break
                        
                        logger.warning(f"Retrying... (attempt {task.attempts}/{task.max_attempts})")
                        await asyncio.sleep(2 ** task.attempts)
                
                except Exception as e:
                    task.error_message = str(e)
                    logger.error(f"Download error (attempt {task.attempts}): {task.track_title} - {e}")
                    
                    # Clean up temporary files
                    if tmp_path and tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except:
                            pass
                    
                    if not task.can_retry:
                        break
                    
                    await asyncio.sleep(2 ** task.attempts)
            
            # All attempts failed
            task.status = DownloadStatus.FAILED
            self.stats["failed"] += 1
            err = task.error_message or "Max retries exceeded"
            logger.error(f"[FAIL] Failed after {task.max_attempts} attempts: {task.track_title} - {err}")
            print(f"   ❌ {task.track_title}: {err}")
            if self.on_progress: self.on_progress(task)
            return False, err
    
    async def _download_with_progress(
        self,
        task: DownloadTask,
        session: aiohttp.ClientSession,
        tmp_path: Path,
        on_chunk: Optional[Callable] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Downloads a file with progress tracking
        """
        try:
            # Create directory
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Download
            async with session.get(task.url, timeout=self.timeout) as response:
                # Check status
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After", "60")
                    return False, f"Rate limited - retry after {retry_after}s"
                
                if response.status == 451:
                    return False, "Geo-blocked (HTTP 451)"
                
                if response.status == 403:
                    return False, "Forbidden - token may be expired (HTTP 403)"
                
                if response.status != 200:
                    return False, f"HTTP {response.status}"
                
                # Check Content-Type (detect error responses)
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/json" in content_type or "text/" in content_type:
                    return False, f"Invalid Content-Type: {content_type}"
                
                # Get size
                total_size = int(response.headers.get('content-length', 0))
                if total_size > 0:
                    task.expected_size = total_size
                
                # Write to file
                task.bytes_downloaded = 0
                
                async with aiofiles.open(tmp_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(self.chunk_size):
                        await f.write(chunk)
                        task.bytes_downloaded += len(chunk)
                        
                        # Callbacks
                        if self.on_progress:
                            self.on_progress(task)
                        if on_chunk:
                            on_chunk(len(chunk))
            
            return True, None
        
        except asyncio.TimeoutError:
            return False, "Download timeout"
        except aiohttp.ClientError as e:
            return False, f"Network error: {str(e)}"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"
    
    async def download_batch(
        self,
        tasks: list[DownloadTask]
    ) -> dict:
        """
        Downloads a batch of files concurrently
        """
        # Sort by priority
        tasks.sort(key=lambda t: t.priority.value, reverse=True)
        
        # Get session
        session = await self._get_session()

        # Create tasks
        download_tasks = [
            self.download_file(task, session)
            for task in tasks
        ]
        
        # Execute concurrently
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        
        # Process results
        for task, result in zip(tasks, results):
            if isinstance(result, Exception):
                task.status = DownloadStatus.FAILED
                task.error_message = str(result)
                self.stats["failed"] += 1
    
        return self.stats
    
    async def download_segments(
        self,
        urls: list[str],
        output_path: Path,
        track_id: Optional[int] = None,
        track_title: Optional[str] = None,
        on_segment: Optional[Callable] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Download multiple HLS segments and concatenate them into a single file.
        Used for video streams where parse_video_stream returns segment URLs.

        Returns:
            tuple[success, error_message]
        """
        async with self.semaphore:
            tmp_path = output_path.with_suffix(output_path.suffix + f".part.{uuid.uuid4().hex[:8]}")
            output_path.parent.mkdir(parents=True, exist_ok=True)

            session = await self._get_session()

            total_segs = len(urls)
            try:
                async with aiofiles.open(tmp_path, "wb") as out_f:
                    for i, url in enumerate(urls):
                        for attempt in range(MAX_RETRIES):
                            try:
                                async with session.get(url, timeout=self.timeout) as response:
                                    if response.status == 429:
                                        wait = min(60 * (attempt + 1), 300)
                                        logger.warning(f"Rate limited on segment {i}, waiting {wait}s...")
                                        await asyncio.sleep(wait)
                                        continue
                                    if response.status != 200:
                                        return False, f"Segment {i}: HTTP {response.status}"
                                    async for chunk in response.content.iter_chunked(self.chunk_size):
                                        await out_f.write(chunk)
                                    if on_segment:
                                        on_segment()
                                        await asyncio.sleep(0)
                                    break  # segment OK
                            except Exception as e:
                                if attempt == MAX_RETRIES - 1:
                                    raise
                                await asyncio.sleep(2 ** attempt)
                shutil.move(str(tmp_path), str(output_path))
                self.stats["completed"] += 1
                return True, None

            except Exception as e:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                self.stats["failed"] += 1
                err = str(e)
                logger.error(f"Segment download failed: {track_title} - {err}")
                return False, err

    def reset_stats(self):
        """Reset download statistics"""
        self.stats = {
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "corrupted": 0,
            "total_bytes": 0,
        }

    def get_stats(self) -> dict:
        """Get download statistics"""
        return {
            **self.stats,
            "success_rate": (
                self.stats["completed"] / 
                max(1, self.stats["completed"] + self.stats["failed"])
            ) * 100
        }