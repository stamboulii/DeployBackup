"""
Tar Stream Downloader - Bulk download via SSH tar pipe
Eliminates per-file SFTP overhead by streaming tar archives over SSH.
"""

import os
import tarfile
import logging
import time
import threading
from typing import List, Optional, Callable, Dict

logger = logging.getLogger(__name__)


class TarStreamDownloader:
    """
    Downloads files from a remote server by streaming tar archives over SSH.
    Dramatically faster than per-file SFTP for bulk downloads (1000+ files),
    because it eliminates per-file SFTP overhead (open/negotiate/close per file).
    """

    def __init__(self, ssh_client, remote_root: str, local_root: str,
                 sftp_client=None):
        """
        Args:
            ssh_client: paramiko.SSHClient instance
            remote_root: Remote base directory to download from
            local_root: Local directory to extract files into
            sftp_client: Optional paramiko.SFTPClient for path verification
        """
        self.ssh = ssh_client
        # Get known entries for chroot path verification
        known_entries = []
        if sftp_client:
            for try_path in ['/', '.', '']:
                try:
                    entries = sftp_client.listdir(try_path)
                    known_entries = [e for e in entries if e not in ('.', '..')][:5]
                    if known_entries:
                        logger.info(f"SFTP root entries (via '{try_path}'): {known_entries}")
                        break
                except Exception:
                    continue
            if not known_entries:
                logger.info("Could not get SFTP root entries for tar path verification")
        self.remote_root = self._resolve_real_path(
            ssh_client, remote_root.rstrip('/'), known_entries
        )
        self.local_root = local_root
        self.stats = {
            'files_extracted': 0,
            'bytes_transferred': 0,
            'errors': [],
            'start_time': None,
            'elapsed': 0,
        }
        self._stop = False
        self._created_dirs = set()

    @staticmethod
    def _resolve_real_path(ssh_client, sftp_path: str,
                           known_entries: Optional[List] = None) -> str:
        """
        Resolve SFTP chroot path to real filesystem path for exec_command.
        SFTP "/" might be chrooted to "/home/user/data/" on the real FS.
        Walks up from $HOME and verifies with known SFTP entries.
        """
        is_root = sftp_path in ('', '/', '/.', '.')

        # Non-root path: try as-is
        if not is_root:
            try:
                stdin, stdout, stderr = ssh_client.exec_command(
                    f'test -d "{sftp_path}" && echo OK', timeout=10
                )
                if b'OK' in stdout.read():
                    return sftp_path
            except Exception:
                pass

        # Get $HOME and walk up parent directories
        try:
            stdin, stdout, stderr = ssh_client.exec_command('echo $HOME', timeout=10)
            home = stdout.read().decode('utf-8', errors='replace').strip()
        except Exception:
            home = None

        if home:
            candidates = []
            current = home.rstrip('/')
            for _ in range(6):
                if not current:
                    break
                candidates.append(current)
                parent = current.rsplit('/', 1)[0] if '/' in current else ''
                if parent == current:
                    break
                current = parent

            valid_candidates = []

            for candidate in candidates:
                test_path = candidate if is_root else candidate + '/' + sftp_path.lstrip('/')

                try:
                    stdin2, stdout2, stderr2 = ssh_client.exec_command(
                        f'test -d "{test_path}" && echo OK', timeout=10
                    )
                    if b'OK' not in stdout2.read():
                        continue
                except Exception:
                    continue

                valid_candidates.append(test_path)

                # Verify with known SFTP entries
                if known_entries:
                    matches = 0
                    for entry in known_entries[:3]:
                        try:
                            stdin3, stdout3, stderr3 = ssh_client.exec_command(
                                f'test -e "{test_path}/{entry}" && echo Y', timeout=5
                            )
                            if b'Y' in stdout3.read():
                                matches += 1
                        except Exception:
                            pass
                    if matches >= min(2, len(known_entries)):
                        logger.info(f"Resolved SFTP path '{sftp_path}' -> '{test_path}' (verified)")
                        return test_path

            # Fallback: no known_entries or verification failed.
            # Pick the candidate with the most content (recursive depth 2).
            if valid_candidates:
                if len(valid_candidates) == 1:
                    best = valid_candidates[0]
                    logger.info(f"Resolved SFTP path '{sftp_path}' -> '{best}' (only candidate)")
                    return best

                best_path = valid_candidates[0]
                best_count = -1
                for vc in valid_candidates:
                    try:
                        stdin4, stdout4, stderr4 = ssh_client.exec_command(
                            f'find -L "{vc}" -maxdepth 2 -mindepth 1 2>/dev/null | head -2000 | wc -l',
                            timeout=15
                        )
                        count_str = stdout4.read().decode('utf-8', errors='replace').strip()
                        count = int(count_str) if count_str.isdigit() else 0
                        logger.info(f"Candidate '{vc}' -> {count} entries (depth<=2)")
                        if count > best_count:
                            best_count = count
                            best_path = vc
                    except Exception:
                        pass
                logger.info(f"Resolved SFTP path '{sftp_path}' -> '{best_path}' (most entries: {best_count})")
                return best_path

        logger.warning(f"Could not resolve real path for '{sftp_path}', using as-is")
        return sftp_path

    def is_available(self) -> bool:
        """Check if tar streaming is possible (server has tar + allows exec_command)"""
        try:
            stdin, stdout, stderr = self.ssh.exec_command('command -v tar', timeout=10)
            out = stdout.read().decode('utf-8', errors='replace').strip()
            exit_code = stdout.channel.recv_exit_status()
            return exit_code == 0 and len(out) > 0
        except Exception as e:
            logger.debug(f"tar availability check failed: {e}")
            return False

    def download_all(self, progress_callback: Optional[Callable] = None,
                     use_compression: bool = True, expected_total: int = 0) -> Dict:
        """
        Download entire remote directory via tar stream.
        Best for initial full backup.
        """
        self.stats['start_time'] = time.time()
        compress_flag = 'z' if use_compression else ''
        cmd = f'tar c{compress_flag}hf - --ignore-failed-read --warning=no-file-changed -C "{self.remote_root}" .'
        logger.info(f"Tar stream (full): {cmd}")

        try:
            self._stream_and_extract(cmd, progress_callback, expected_total)
        except Exception as e:
            logger.error(f"Tar stream (full) failed: {e}")
            self.stats['errors'].append(f"stream error: {e}")

        self.stats['elapsed'] = time.time() - self.stats['start_time']
        return self.stats

    def download_files(self, file_list: List[str],
                       progress_callback: Optional[Callable] = None,
                       use_compression: bool = True,
                       sftp_client=None) -> Dict:
        """
        Download specific files via tar stream (for incremental updates).

        Uses a tiered strategy for maximum reliability:
          1. Write file list to a temp file on the server, then
             ``tar -T /tmp/filelist``.  No stdin, no ARG_MAX — most robust.
          2. If the server has no writable temp dir, fall back to passing
             files as command-line arguments (batched for ARG_MAX).
          3. Individual file errors are collected in ``self.stats['errors']``
             so the caller can retry them via per-file SFTP.

        Args:
            file_list: List of relative paths (from remote_root)
            sftp_client: Optional paramiko.SFTPClient for writing the temp
                         file list (tier 1).  If None, skips to tier 2.
        """
        self.stats['start_time'] = time.time()
        if not file_list:
            return self.stats

        compress_flag = 'z' if use_compression else ''

        # ── TIER 1: temp file on server (most robust) ──────────────────
        if sftp_client and self._try_filelist_on_server(
                sftp_client, file_list, compress_flag,
                progress_callback):
            self.stats['elapsed'] = time.time() - self.stats['start_time']
            return self.stats

        # ── TIER 2: command-line arguments (batched for ARG_MAX) ───────
        logger.info("Falling back to tar with command-line arguments")
        self._download_via_args(file_list, compress_flag, progress_callback)

        self.stats['elapsed'] = time.time() - self.stats['start_time']
        return self.stats

    # ── Tier 1 helper: write file list to server, tar -T <file> ────────

    def _try_filelist_on_server(self, sftp_client, file_list: List[str],
                                compress_flag: str,
                                progress_callback: Optional[Callable]) -> bool:
        """
        Write the file list to a temp file on the server and run
        ``tar -T <tmpfile>``.  Returns True if it succeeded (even
        partially), False if the temp-file approach is not possible
        and we should fall back to tier 2.
        """
        # Use null-separated paths for maximum safety (handles any filename)
        filelist_content = '\0'.join(file_list).encode('utf-8') + b'\0'

        # Find a writable directory on the server
        remote_tmp = self._find_writable_dir(sftp_client)
        if not remote_tmp:
            logger.info("No writable temp dir found on server, skipping tier 1")
            return False

        import uuid
        tmp_name = f"{remote_tmp}/.synergy_filelist_{uuid.uuid4().hex[:8]}.txt"

        try:
            # Write file list via SFTP
            with sftp_client.open(tmp_name, 'wb') as f:
                f.write(filelist_content)
            logger.info(f"Wrote {len(file_list)} paths to {tmp_name}")

            cmd = (f'tar c{compress_flag}hf - --ignore-failed-read '
                   f'--warning=no-file-changed --null '
                   f'-C "{self.remote_root}" -T "{tmp_name}"')
            logger.info(f"Tar stream (filelist): {cmd}")

            self._stream_and_extract(cmd, progress_callback, len(file_list))
            return True

        except Exception as e:
            logger.warning(f"Tar via temp file failed: {e}")
            # Partial success is still success — errors are in self.stats
            if self.stats['files_extracted'] > 0:
                return True
            return False

        finally:
            # Clean up temp file
            try:
                sftp_client.remove(tmp_name)
                logger.debug(f"Cleaned up {tmp_name}")
            except Exception:
                pass

    @staticmethod
    def _find_writable_dir(sftp_client) -> Optional[str]:
        """Find a directory we can write a temp file to on the server."""
        candidates = ['/tmp', '/var/tmp', '.']
        for d in candidates:
            try:
                # Try to create and immediately remove a test file
                test_path = f"{d}/.synergy_write_test"
                with sftp_client.open(test_path, 'w') as f:
                    f.write('test')
                sftp_client.remove(test_path)
                return d
            except Exception:
                continue
        return None

    # ── Tier 2 helper: pass files as shell arguments (batched) ─────────

    def _download_via_args(self, file_list: List[str],
                           compress_flag: str,
                           progress_callback: Optional[Callable]):
        """
        Pass file paths as tar command-line arguments, batched to stay
        under the shell ARG_MAX limit (~128 KB).
        Filenames are escaped to handle spaces, quotes, and special chars.
        """
        ARG_MAX_SAFE = 100_000  # bytes

        batches = []
        current_batch = []
        current_size = 0

        for path in file_list:
            # Shell-escape: replace ' with '\'' for safe single-quoting
            escaped = path.replace("'", "'\\''")
            path_size = len(escaped.encode('utf-8')) + 3  # 'escaped' + space
            if current_batch and current_size + path_size > ARG_MAX_SAFE:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            current_batch.append(escaped)
            current_size += path_size

        if current_batch:
            batches.append(current_batch)

        logger.info(f"Tar stream (args): {len(file_list)} files in {len(batches)} batch(es)")

        for batch_idx, batch in enumerate(batches):
            if self._stop:
                break

            escaped_files = ' '.join(f"'{f}'" for f in batch)
            cmd = (f'tar c{compress_flag}hf - --ignore-failed-read '
                   f'--warning=no-file-changed -C "{self.remote_root}" {escaped_files}')

            if len(batches) > 1:
                logger.info(f"Tar batch {batch_idx + 1}/{len(batches)}: {len(batch)} files")

            try:
                self._stream_and_extract(cmd, progress_callback, len(file_list))
            except Exception as e:
                logger.error(f"Tar stream (args, batch {batch_idx + 1}) failed: {e}")
                self.stats['errors'].append(f"stream error (batch {batch_idx + 1}): {e}")

    def _ensure_dir(self, dir_path: str):
        """Create directory with caching"""
        if dir_path in self._created_dirs:
            return
        os.makedirs(dir_path, exist_ok=True)
        self._created_dirs.add(dir_path)

    @staticmethod
    def _normalize_member_name(name: str) -> str:
        """Strip leading ./ from tar member names"""
        if name.startswith('./'):
            name = name[2:]
        return name

    def _stream_and_extract(self, cmd: str, progress_callback: Optional[Callable],
                            expected_total: int, stdin_data: bytes = None):
        """Core: run remote tar, stream output, extract locally"""
        # Set SSH keepalive to prevent timeout during long transfers
        transport = self.ssh.get_transport()
        if transport:
            transport.set_keepalive(30)

        stdin, stdout, stderr = self.ssh.exec_command(cmd, bufsize=65536)

        if stdin_data:
            # Write stdin in a background thread to prevent deadlock:
            # tar writes to stdout while we write file paths to stdin.
            # If both channel buffers fill up with nobody reading the other
            # side, both processes block forever → "Socket is closed".
            def _write_stdin():
                try:
                    offset = 0
                    chunk_size = 65536
                    while offset < len(stdin_data):
                        stdin.write(stdin_data[offset:offset + chunk_size])
                        offset += chunk_size
                    stdin.channel.shutdown_write()
                except OSError:
                    # Channel/socket already closed by the remote side — this
                    # is expected when tar exits early (e.g. broken symlinks).
                    pass
                except Exception as e:
                    logger.debug(f"stdin write finished early: {e}")
                finally:
                    try:
                        stdin.channel.shutdown_write()
                    except Exception:
                        pass

            writer = threading.Thread(target=_write_stdin, daemon=True)
            writer.start()

        # Detect tar mode from command flags
        tar_mode = 'r|gz' if 'z' in cmd.split()[1] else 'r|'

        with tarfile.open(fileobj=stdout, mode=tar_mode) as tar:
            for member in tar:
                if self._stop:
                    break

                member_name = self._normalize_member_name(member.name)

                if not member.isfile():
                    if member.isdir() and member_name:
                        self._ensure_dir(os.path.join(self.local_root, member_name))
                    continue

                if not member_name:
                    continue

                try:
                    local_path = os.path.join(self.local_root, member_name)
                    self._ensure_dir(os.path.dirname(local_path))

                    source = tar.extractfile(member)
                    if source:
                        with open(local_path, 'wb') as out:
                            while True:
                                chunk = source.read(65536)
                                if not chunk:
                                    break
                                out.write(chunk)
                        source.close()

                    self.stats['files_extracted'] += 1
                    self.stats['bytes_transferred'] += member.size

                    if progress_callback and self.stats['files_extracted'] % 100 == 0:
                        progress_callback(
                            self.stats['files_extracted'],
                            expected_total,
                            self._get_speed_stats()
                        )

                except Exception as e:
                    logger.warning(f"Extract failed: {member_name}: {e}")
                    self.stats['errors'].append(member_name)

        # Final progress callback
        if progress_callback:
            progress_callback(
                self.stats['files_extracted'],
                expected_total,
                self._get_speed_stats()
            )

        # Log stderr warnings and clean up SSH channel.
        # Filter out noise that --ignore-failed-read already handled.
        _STDERR_NOISE = (
            'Removing leading',
            'stat impossible',          # FR locale: broken symlink / vanished file
            'Cannot stat',              # EN locale: same
            'file changed as we read',  # file modified during tar
            'No such file or directory',
            'Aucun fichier ou dossier',
        )
        try:
            err = stderr.read().decode('utf-8', errors='replace').strip()
            if err:
                for line in err.splitlines():
                    if any(noise in line for noise in _STDERR_NOISE):
                        logger.debug(f"tar (ignored): {line}")
                        continue
                    if line.strip():
                        logger.warning(f"tar: {line}")
        except Exception:
            pass

        # Explicitly close streams to prevent "Socket is closed" noise on GC.
        # Order matters: shut down stdin write direction first so the remote
        # tar process receives EOF, then drain stderr, and finally close all
        # three streams.  We also close the underlying channel to ensure
        # Paramiko's BufferedFile.__del__ won't attempt a late flush on an
        # already-dead socket.
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass

        for s in (stdin, stdout, stderr):
            try:
                s.close()
            except Exception:
                pass

        # Close the channel itself so no deferred __del__ can trigger
        # "Socket is closed" tracebacks.
        try:
            stdout.channel.close()
        except Exception:
            pass

    def _get_speed_stats(self) -> Dict:
        elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 1
        if elapsed <= 0:
            elapsed = 0.001
        return {
            'mbps': self.stats['bytes_transferred'] / elapsed / (1024 * 1024),
            'files_per_second': self.stats['files_extracted'] / elapsed,
            'bytes_transferred': self.stats['bytes_transferred'],
            'elapsed': elapsed,
        }

    def verify_extraction(self, expected_files: Dict[str, int]) -> List[str]:
        """
        Verify extracted files exist and match expected sizes.

        Args:
            expected_files: {rel_path: expected_size}

        Returns:
            List of rel_paths that failed verification
        """
        failed = []
        for rel_path, expected_size in expected_files.items():
            local_path = os.path.join(self.local_root, rel_path)
            if not os.path.exists(local_path):
                failed.append(rel_path)
            elif expected_size > 0:
                try:
                    actual = os.path.getsize(local_path)
                    if actual != expected_size:
                        failed.append(rel_path)
                except OSError:
                    failed.append(rel_path)
        return failed

    def stop(self):
        self._stop = True
