"""
Incremental Scanner - Optimized FTP Scanner
Scan intelligent avec cache et détection des changements uniquement
"""

import os
import time
from ftplib import FTP
from datetime import datetime, timedelta
from typing import Dict, Set, Optional, Tuple, List, Callable
from dataclasses import dataclass
import hashlib
import logging

logger = logging.getLogger(__name__)


@dataclass
class ScanCache:
    """Cache des scans précédents"""
    directories: Dict[str, datetime]  # {path: last_modified}
    files: Dict[str, Dict]  # {rel_path: {size, modify}}
    last_full_scan: datetime
    scan_strategy: str  # 'full', 'incremental', 'smart'


def _is_connection_dead(error_msg: str) -> bool:
    """Check if an error indicates a dead connection"""
    indicators = [
        'socket is closed', 'socket', 'broken pipe', 'reset',
        'timed out', 'eof', 'connection', '10054', '10053',
        'transport', 'channel closed', 'not connected'
    ]
    msg = str(error_msg).lower()
    return any(ind in msg for ind in indicators)


class IncrementalScanner:
    """
    Scanner FTP optimisé qui évite de scanner tout le serveur
    Utilise un cache et ne scanne que les dossiers modifiés
    """

    def __init__(self,
                 ftp: FTP,
                 remote_root: str,
                 cache_file: Optional[str] = None,
                 incremental_threshold_hours: int = 24,
                 reconnect_factory: Optional[Callable] = None):
        """
        Args:
            ftp: Connexion FTP active
            remote_root: Racine du scan
            cache_file: Fichier pour persister le cache
            incremental_threshold_hours: Si dernier scan < X heures, scan incrémental
            reconnect_factory: Callable that returns a new FTP/SFTP connection
        """
        self.ftp = ftp
        self.remote_root = remote_root
        self.cache_file = cache_file
        self.incremental_threshold = timedelta(hours=incremental_threshold_hours)
        self.reconnect_factory = reconnect_factory
        self._reconnect_count = 0

        self.cache = self._load_cache()
        self.scan_stats = {
            'dirs_scanned': 0,
            'files_found': 0,
            'cache_hits': 0,
            'strategy': 'unknown',
            'reconnections': 0,
            'scan_errors': 0
        }

    def _reconnect(self) -> bool:
        """Attempt to reconnect the FTP/SFTP connection"""
        if not self.reconnect_factory:
            return False

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Close old connection
                try:
                    self.ftp.quit()
                except Exception:
                    try:
                        self.ftp.close()
                    except Exception:
                        pass

                # Create new connection
                self.ftp = self.reconnect_factory()
                self._reconnect_count += 1
                self.scan_stats['reconnections'] += 1
                logger.info(f"Scanner reconnected (attempt {attempt + 1}, total reconnections: {self._reconnect_count})")
                return True
            except Exception as e:
                logger.warning(f"Scanner reconnect attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 3)

        return False

    def _is_connection_alive(self) -> bool:
        """Check if current connection is still alive"""
        try:
            self.ftp.voidcmd('NOOP')
            return True
        except Exception:
            return False

    def _load_cache(self) -> ScanCache:
        """Charge le cache depuis le disque"""
        if self.cache_file and os.path.exists(self.cache_file):
            try:
                import pickle
                with open(self.cache_file, 'rb') as f:
                    return pickle.load(f)
            except Exception as e:
                logger.debug(f"Failed to load cache: {e}")

        # Cache vide
        return ScanCache(
            directories={},
            files={},
            last_full_scan=datetime.min,
            scan_strategy='full'
        )

    def _save_cache(self):
        """Sauvegarde le cache sur le disque"""
        if self.cache_file:
            try:
                import pickle
                dirname = os.path.dirname(self.cache_file)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
                with open(self.cache_file, 'wb') as f:
                    pickle.dump(self.cache, f)
            except Exception as e:
                logger.warning(f"Failed to save cache '{self.cache_file}': {e}")

    def _should_use_incremental(self) -> bool:
        """Détermine si on peut faire un scan incrémental"""
        time_since_last = datetime.now() - self.cache.last_full_scan

        # Si dernier full scan < threshold et on a du cache
        return (time_since_last < self.incremental_threshold and
                len(self.cache.files) > 0)

    def _get_directory_mtime(self, dir_path: str) -> Optional[datetime]:
        """
        Obtient la date de modification d'un dossier
        Note: Pas supporté par tous les serveurs FTP
        """
        try:
            # Essayer MDTM (modification time)
            response = self.ftp.sendcmd(f'MDTM {dir_path}')
            # Format: 213 YYYYMMDDhhmmss
            if response.startswith('213'):
                time_str = response.split()[1]
                return datetime.strptime(time_str, '%Y%m%d%H%M%S')
        except:
            pass

        return None

    def _scan_directory_mlsd(self, dir_path: str) -> Tuple[List[Tuple], bool]:
        """
        Scan un dossier avec MLSD (plus moderne et fiable)
        Détecte et skip les symlinks pour éviter les problèmes de taille
        Returns: (items, supports_mlsd)
        """
        try:
            items = []
            self.ftp.retrlines(f'MLSD {dir_path}', items.append)

            parsed_items = []
            for item in items:
                parts = item.split(';')
                name = parts[-1].strip()

                if name in ('.', '..'):
                    continue

                props = {}
                for part in parts[:-1]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        props[key] = value
                
                # Détecter les symlinks (type=lnk) et les skipper
                item_type = props.get('type', '')
                if item_type == 'lnk':
                    logger.debug(f"Skipping symlink: {name}")
                    continue
                    
                # Détecter les liens via 'unique' ou d'autres indicateurs
                if props.get('unique') and props.get('type') != 'dir' and props.get('type') != 'file':
                    # Peut être un lien symbolique
                    logger.debug(f"Skipping potential link: {name} (type={item_type})")
                    continue

                parsed_items.append((name, props))

            return parsed_items, True

        except Exception:
            return [], False

    def _scan_directory_list(self, dir_path: str) -> List[Tuple]:
        """
        Scan un dossier avec LIST (fallback)
        Détecte et skip les symlinks pour éviter les problèmes de taille
        Returns: [(name, props)]
        """
        try:
            self.ftp.cwd(dir_path)
            items = []
            self.ftp.dir(items.append)

            parsed_items = []
            for item in items:
                parts = item.split(None, 8)
                if len(parts) < 9:
                    continue

                permissions = parts[0]
                size = parts[4]
                name = parts[8]

                if name in ('.', '..'):
                    continue
                
                # Détecter les symlinks (permissions commencent par 'l')
                if permissions.startswith('l'):
                    logger.debug(f"Skipping symlink: {name}")
                    continue

                props = {
                    'type': 'dir' if permissions.startswith('d') else 'file',
                    'size': size,
                    'modify': ''  # LIST ne donne pas de timestamp fiable
                }

                parsed_items.append((name, props))

            return parsed_items

        except Exception as e:
            logger.warning(f"Error scanning {dir_path}: {e}")
            return []

    def _scan_directory_with_reconnect(self, dir_path: str, use_mlsd: bool = True) -> List[Tuple]:
        """Scan a directory, reconnecting if the connection is dead"""
        # First attempt
        items = self._scan_directory(dir_path, use_mlsd)
        if items:
            return items

        # If empty result, check if connection is dead
        if not self._is_connection_alive():
            logger.warning(f"Connection lost during scan at {dir_path}, attempting reconnect...")
            if self._reconnect():
                # Retry after reconnection
                items = self._scan_directory(dir_path, use_mlsd)
                return items
            else:
                logger.error("Failed to reconnect during scan")

        return items

    def _scan_directory(self, dir_path: str, use_mlsd: bool = True) -> List[Tuple]:
        """Scan un dossier avec la meilleure méthode disponible"""
        if use_mlsd:
            items, mlsd_ok = self._scan_directory_mlsd(dir_path)
            if mlsd_ok:
                return items

        # Fallback to LIST
        return self._scan_directory_list(dir_path)

    def _scan_recursive(self,
                       base_path: str,
                       relative_path: str = '',
                       use_mlsd: bool = True,
                       status_callback=None) -> Dict[str, Dict]:
        """
        Scan récursif d'un dossier avec reconnection automatique
        """
        files = {}
        current_path = os.path.join(base_path, relative_path).replace('\\', '/')

        self.scan_stats['dirs_scanned'] += 1

        if status_callback and self.scan_stats['dirs_scanned'] % 10 == 0:
            status_callback(self.scan_stats)

        items = self._scan_directory_with_reconnect(current_path, use_mlsd)

        if not items and not self._is_connection_alive():
            # Connection died and reconnect failed or not available
            self.scan_stats['scan_errors'] += 1
            return files

        for name, props in items:
            rel_path = os.path.join(relative_path, name).replace('\\', '/')

            if props.get('type') == 'dir':
                # Récursion dans les sous-dossiers
                sub_files = self._scan_recursive(base_path, rel_path, use_mlsd, status_callback)
                files.update(sub_files)
            else:
                # Fichier
                files[rel_path] = {
                    'size': int(props.get('size', 0)),
                    'modify': props.get('modify', ''),
                }
                self.scan_stats['files_found'] += 1

        return files

    def scan_full(self, status_callback=None) -> Dict[str, Dict]:
        """
        Scan complet du serveur FTP
        """
        self.scan_stats = {
            'dirs_scanned': 0,
            'files_found': 0,
            'cache_hits': 0,
            'strategy': 'full',
            'reconnections': 0,
            'scan_errors': 0
        }

        files = self._scan_recursive(self.remote_root, '', True, status_callback)

        # Only mark as a complete full scan if there were no errors
        # Otherwise the next run would use incremental mode and skip
        # all the directories that were missed due to connection issues
        self.cache.files = files
        if self.scan_stats['scan_errors'] == 0:
            self.cache.last_full_scan = datetime.now()
            self.cache.scan_strategy = 'full'
        else:
            # Keep old last_full_scan so next run does a full scan again
            self.cache.scan_strategy = 'partial'
            logger.warning(
                f"Scan had {self.scan_stats['scan_errors']} errors — "
                f"cache saved as partial, next run will do a full scan"
            )
        self._save_cache()

        return files

    def scan_incremental(self, status_callback=None) -> Dict[str, Dict]:
        """
        Scan incrémental : ne scanne que les dossiers potentiellement modifiés
        Beaucoup plus rapide pour les gros serveurs
        """
        self.scan_stats = {
            'dirs_scanned': 0,
            'files_found': 0,
            'cache_hits': 0,
            'strategy': 'incremental',
            'reconnections': 0,
            'scan_errors': 0
        }

        if not self.cache.files:
            # Pas de cache, faire un full scan
            return self.scan_full(status_callback)

        # Stratégie : Scanner le dossier root et comparer avec le cache
        # Si différences → scanner ces sous-dossiers uniquement

        # 1. Scanner le niveau root
        root_items = self._scan_directory_with_reconnect(self.remote_root, use_mlsd=True)

        # 2. Détecter les dossiers nouveaux ou potentiellement modifiés
        current_dirs = {name for name, props in root_items if props.get('type') == 'dir'}
        cached_dirs = {os.path.dirname(f) for f in self.cache.files.keys() if '/' in f}

        # 3. Dossiers à scanner
        new_dirs = current_dirs - cached_dirs

        # 4. Pour un scan vraiment incrémental, on pourrait vérifier les mtimes
        # Mais beaucoup de serveurs FTP ne supportent pas MDTM sur les dossiers
        # Donc on fait un compromis : scanner tous les dossiers de premier niveau

        files = dict(self.cache.files)  # Copier le cache

        # Scanner les nouveaux dossiers et mettre à jour les fichiers root
        for name, props in root_items:
            rel_path = name

            if props.get('type') == 'dir':
                if name in new_dirs:
                    # Nouveau dossier : scanner complètement
                    sub_files = self._scan_recursive(self.remote_root, rel_path, True, status_callback)
                    files.update(sub_files)
                else:
                    # Dossier existant : on garde le cache (optimisation)
                    self.scan_stats['cache_hits'] += 1
            else:
                # Fichier à la racine
                files[rel_path] = {
                    'size': int(props.get('size', 0)),
                    'modify': props.get('modify', ''),
                }
                self.scan_stats['files_found'] += 1

        # Mettre à jour le cache
        self.cache.files = files
        self.cache.scan_strategy = 'incremental'
        self._save_cache()

        return files

    @staticmethod
    def _resolve_real_path(ssh_client, sftp_path: str,
                           known_entries: Optional[List[str]] = None) -> Optional[str]:
        """
        Resolve an SFTP path to its real filesystem path for use with exec_command.

        SFTP sessions are often chrooted — SFTP "/" might map to "/home/user/data/"
        on the real filesystem. exec_command runs in the real shell, so we need
        the actual path.

        Args:
            ssh_client: paramiko SSHClient
            sftp_path: The SFTP path (may be chrooted)
            known_entries: File/directory names known to exist at the SFTP root
                          (used to verify candidates)
        """
        is_root = sftp_path in ('', '/', '/.', '.')

        # Strategy 1: Non-root path — try as-is
        if not is_root:
            try:
                stdin, stdout, stderr = ssh_client.exec_command(
                    f'test -d "{sftp_path}" && echo OK', timeout=10
                )
                if b'OK' in stdout.read():
                    return sftp_path
            except Exception:
                pass

        # Strategy 2: Get $HOME and walk up parent directories.
        # The chroot root is often a parent of $HOME.
        # e.g., $HOME = /homez.2213/user/www/PACKAGE
        #        chroot root = /homez.2213/user
        try:
            stdin, stdout, stderr = ssh_client.exec_command('echo $HOME', timeout=10)
            home = stdout.read().decode('utf-8', errors='replace').strip()
        except Exception:
            home = None

        if not home:
            # Try pwd as fallback
            try:
                stdin, stdout, stderr = ssh_client.exec_command('pwd', timeout=10)
                home = stdout.read().decode('utf-8', errors='replace').strip()
            except Exception:
                pass

        if home:
            # Build list of candidates: $HOME, $HOME/.., $HOME/../.., etc.
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
                if is_root:
                    test_path = candidate
                else:
                    test_path = candidate + '/' + sftp_path.lstrip('/')

                # Check directory exists
                try:
                    stdin2, stdout2, stderr2 = ssh_client.exec_command(
                        f'test -d "{test_path}" && echo OK', timeout=10
                    )
                    if b'OK' not in stdout2.read():
                        continue
                except Exception:
                    continue

                valid_candidates.append(test_path)

                # If we have known SFTP entries, verify they exist here
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
                        logger.info(f"Resolved SFTP path '{sftp_path}' -> '{test_path}' (verified with {matches} entries)")
                        return test_path

            # Fallback: no known_entries or verification failed.
            # Pick the candidate with the most content. Use recursive find
            # (maxdepth 2) because the chroot root has thousands of files in
            # subdirectories, while a deep subdirectory like $HOME may have
            # more top-level entries but far fewer total files.
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

        logger.warning(f"Could not resolve real path for SFTP path '{sftp_path}'")
        return None

    def scan_ssh_find(self, status_callback=None) -> Optional[Dict[str, Dict]]:
        """
        Fast scan using SSH 'find' command instead of per-directory SFTP listings.
        Runs a single 'find' command that returns all files at once — turns a 5+ hour
        scan into minutes for servers with hundreds of thousands of files.

        Returns:
            Dict of {rel_path: {size, modify}} on success, or None if SSH find
            is not available (caller should fall back to regular scan).
        """
        # Check if the FTP connection has an SSH client (SFTPAdapter)
        ssh_client = getattr(self.ftp, 'ssh', None)
        if not ssh_client:
            logger.debug("scan_ssh_find: no SSH client available")
            return None

        self.scan_stats = {
            'dirs_scanned': 0,
            'files_found': 0,
            'cache_hits': 0,
            'strategy': 'ssh_find',
            'reconnections': 0,
            'scan_errors': 0
        }

        remote_root = self.remote_root.rstrip('/')

        try:
            # Set keepalive to prevent timeout during large scans
            transport = ssh_client.get_transport()
            if transport:
                transport.set_keepalive(30)

            # Get a few known entries from SFTP root for path verification.
            # This helps resolve chrooted paths by checking which real directory
            # contains these known files/dirs.
            known_entries = []
            sftp_client = getattr(self.ftp, 'sftp', None)
            if sftp_client:
                # Try multiple path variants — some servers are picky about '/' vs '.' vs ''
                for try_path in ['/', '.', '']:
                    try:
                        entries = sftp_client.listdir(try_path)
                        known_entries = [e for e in entries if e not in ('.', '..')][:5]
                        if known_entries:
                            logger.info(f"SFTP root entries (via '{try_path}'): {known_entries}")
                            break
                    except Exception:
                        continue

            # Fallback: use the SFTPAdapter's robust listing (has SSH exec fallback)
            if not known_entries and hasattr(self.ftp, '_list_files'):
                try:
                    attrs = self.ftp._list_files('/')
                    known_entries = [a.filename for a in attrs
                                    if a.filename not in ('.', '..')][:5]
                    if known_entries:
                        logger.info(f"SFTP root entries (via adapter): {known_entries}")
                except Exception:
                    pass

            if not known_entries:
                logger.info("Could not get SFTP root entries for path verification")

            # Resolve the real filesystem path for exec_command.
            # SFTP may be chrooted (e.g., SFTP "/" = real "/home/user/data/"),
            # but exec_command runs in the real shell, so we need the real path.
            real_root = self._resolve_real_path(ssh_client, remote_root, known_entries)
            if not real_root:
                logger.warning("scan_ssh_find: could not resolve real path")
                return None

            # Use find with -printf for efficient output (GNU find)
            # %P = relative path, %s = size in bytes, %T@ = mtime as epoch seconds
            cmd = f'find -L "{real_root}" -type f -printf "%P\\t%s\\t%T@\\n"'
            logger.info(f"SSH find scan: {cmd}")

            stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=3600)

            files = {}
            line_buffer = b''
            bytes_read = 0

            # Stream output line by line to handle 400K+ files without issues
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    break
                bytes_read += len(chunk)
                line_buffer += chunk

                # Process complete lines
                while b'\n' in line_buffer:
                    line_bytes, line_buffer = line_buffer.split(b'\n', 1)
                    line = line_bytes.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue

                    parts = line.split('\t', 2)
                    if len(parts) != 3:
                        continue

                    rel_path, size_str, mtime_str = parts

                    # Skip empty relative path (the root directory itself)
                    if not rel_path:
                        continue

                    # Normalize path separators
                    rel_path = rel_path.replace('\\', '/')

                    try:
                        size = int(size_str)
                    except ValueError:
                        size = 0

                    # Convert epoch timestamp to MLSD format (YYYYMMDDHHMMSS)
                    try:
                        epoch = float(mtime_str)
                        modify = datetime.fromtimestamp(epoch).strftime('%Y%m%d%H%M%S')
                    except (ValueError, OSError):
                        modify = ''

                    files[rel_path] = {
                        'size': size,
                        'modify': modify,
                    }
                    self.scan_stats['files_found'] += 1

                    # Progress callback every 5000 files
                    if status_callback and self.scan_stats['files_found'] % 5000 == 0:
                        status_callback(self.scan_stats)

            # Process any remaining data in buffer
            if line_buffer:
                line = line_buffer.decode('utf-8', errors='replace').strip()
                if line:
                    parts = line.split('\t', 2)
                    if len(parts) == 3:
                        rel_path, size_str, mtime_str = parts
                        if rel_path:
                            rel_path = rel_path.replace('\\', '/')
                            try:
                                size = int(size_str)
                            except ValueError:
                                size = 0
                            try:
                                epoch = float(mtime_str)
                                modify = datetime.fromtimestamp(epoch).strftime('%Y%m%d%H%M%S')
                            except (ValueError, OSError):
                                modify = ''
                            files[rel_path] = {'size': size, 'modify': modify}
                            self.scan_stats['files_found'] += 1

            # Check for errors (e.g., find: -printf not supported)
            exit_code = stdout.channel.recv_exit_status()
            err_output = stderr.read().decode('utf-8', errors='replace').strip()

            if exit_code != 0 and not files:
                # find command failed entirely (maybe -printf not supported)
                logger.warning(f"SSH find failed (exit {exit_code}): {err_output}")
                return None

            if err_output:
                # Log warnings but don't fail (permission denied on some dirs is OK)
                error_lines = err_output.splitlines()
                real_errors = [l for l in error_lines if 'Permission denied' not in l]
                if real_errors:
                    for line in real_errors[:5]:
                        logger.warning(f"find: {line}")
                    self.scan_stats['scan_errors'] = len(real_errors)

            # Final progress callback
            if status_callback:
                status_callback(self.scan_stats)

            logger.info(f"SSH find scan completed: {len(files)} files found")

            # Update cache
            self.cache.files = files
            if self.scan_stats['scan_errors'] == 0:
                self.cache.last_full_scan = datetime.now()
                self.cache.scan_strategy = 'ssh_find'
            else:
                self.cache.scan_strategy = 'partial'
            self._save_cache()

            return files

        except Exception as e:
            logger.warning(f"SSH find scan failed: {e}")
            return None

    def scan_smart(self, status_callback=None) -> Dict[str, Dict]:
        """
        Scan intelligent : choisit automatiquement entre full et incrémental.
        For SFTP connections, tries SSH find first (fastest method).
        """
        # Try SSH find first for SFTP connections (single command, minutes vs hours)
        if hasattr(self.ftp, 'ssh') and self.ftp.ssh:
            logger.info("Trying SSH find scan (fastest for SFTP)")
            result = self.scan_ssh_find(status_callback)
            if result is not None:
                return result
            logger.info("SSH find not available, falling back to directory scan")

        if self._should_use_incremental():
            logger.info("Using incremental scan (fast)")
            return self.scan_incremental(status_callback)
        else:
            logger.info("Using full scan (cache expired or first scan)")
            return self.scan_full(status_callback)

    def get_statistics(self) -> Dict:
        """Retourne les statistiques du dernier scan"""
        return {
            **self.scan_stats,
            'cache_size': len(self.cache.files),
            'last_full_scan': self.cache.last_full_scan.isoformat() if self.cache.last_full_scan > datetime.min else None
        }


class ChunkedScanner:
    """
    Scanner qui divise le travail en chunks pour les très gros serveurs
    Utile pour traiter 1M+ fichiers par batches
    """

    def __init__(self, ftp: FTP, remote_root: str, chunk_size: int = 10000):
        self.ftp = ftp
        self.remote_root = remote_root
        self.chunk_size = chunk_size

    def scan_in_chunks(self, chunk_callback):
        """
        Scanne par chunks et appelle le callback pour chaque chunk

        Args:
            chunk_callback: function(chunk_files: Dict, chunk_num: int, is_last: bool)
        """
        chunk_num = 0
        current_chunk = {}

        def process_directory(dir_path, relative_path=''):
            nonlocal chunk_num, current_chunk

            full_path = os.path.join(dir_path, relative_path).replace('\\', '/')

            try:
                self.ftp.cwd(full_path)
                items = []
                self.ftp.retrlines('MLSD', items.append)

                for item in items:
                    parts = item.split(';')
                    name = parts[-1].strip()

                    if name in ('.', '..'):
                        continue

                    props = {p.split('=')[0]: p.split('=')[1] for p in parts[:-1] if '=' in p}
                    rel_path = os.path.join(relative_path, name).replace('\\', '/')

                    if props.get('type') == 'dir':
                        process_directory(dir_path, rel_path)
                    else:
                        current_chunk[rel_path] = {
                            'size': int(props.get('size', 0)),
                            'modify': props.get('modify', ''),
                        }

                        # Si chunk plein, appeler le callback
                        if len(current_chunk) >= self.chunk_size:
                            chunk_callback(current_chunk, chunk_num, False)
                            chunk_num += 1
                            current_chunk = {}

            except Exception as e:
                logger.warning(f"Error in {full_path}: {e}")

        # Démarrer le scan
        process_directory(self.remote_root)

        # Dernier chunk
        if current_chunk:
            chunk_callback(current_chunk, chunk_num, True)
