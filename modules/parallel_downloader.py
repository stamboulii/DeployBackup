"""
Parallel Downloader - Multi-threaded FTP Download Manager
Optimisé pour télécharger des milliers de fichiers en parallèle
"""

import os
import queue
import threading
import logging
from ftplib import FTP
from dataclasses import dataclass
from typing import List, Callable, Optional, Dict
from datetime import datetime
import time
from modules.sftp_adapter import SFTPAdapter

logger = logging.getLogger(__name__)


@dataclass
class DownloadTask:
    """Représente une tâche de téléchargement"""
    rel_path: str
    remote_path: str
    local_path: str
    size: int
    priority: int = 0  # Plus petit = plus prioritaire
    retry_count: int = 0


@dataclass
class DownloadResult:
    """Résultat d'un téléchargement"""
    rel_path: str
    success: bool
    size: int
    duration: float
    error_message: Optional[str] = None
    retry_count: int = 0


# Errors that indicate a dead connection worth reconnecting
_CONNECTION_ERRORS = (
    ConnectionError, ConnectionResetError, ConnectionAbortedError,
    BrokenPipeError, OSError, EOFError, TimeoutError,
)


class ParallelDownloader:
    """
    Téléchargeur parallèle avec pool de workers FTP
    Optimisé pour télécharger 100K+ fichiers efficacement
    """

    def __init__(self,
                 ftp_host: str,
                 ftp_port: int,
                 ftp_user: str,
                 ftp_pass: str,
                 num_workers: int = 10,
                 max_retries: int = 3,
                 verify_integrity: bool = True):
        """
        Args:
            num_workers: Nombre de connexions FTP simultanées
            max_retries: Nombre de tentatives par fichier
            verify_integrity: Vérifier la taille après download
        """
        self.ftp_host = ftp_host
        self.ftp_port = ftp_port
        self.ftp_user = ftp_user
        self.ftp_pass = ftp_pass
        self.num_workers = num_workers
        self.max_retries = max_retries
        self.verify_integrity = verify_integrity

        # Queues
        self.task_queue = queue.PriorityQueue()
        self.result_queue = queue.Queue()

        # Statistics
        self.stats = {
            'total_files': 0,
            'completed': 0,
            'failed': 0,
            'bytes_transferred': 0,
            'start_time': None,
            'workers_active': 0,
            'reconnections': 0
        }
        self.stats_lock = threading.Lock()

        # Control
        self.stop_flag = threading.Event()
        self.workers = []
        self._is_sftp = (ftp_port == 22)

        # Cache for created directories — avoids repeated os.makedirs calls
        self._created_dirs = set()
        self._dirs_lock = threading.Lock()

    def _create_ftp_connection(self) -> FTP:
        """Crée une nouvelle connexion FTP/SFTP avec retry"""
        max_connect_retries = 3
        for attempt in range(max_connect_retries):
            try:
                if self.ftp_port == 22:
                    ftp = SFTPAdapter(timeout=300)
                else:
                    ftp = FTP(timeout=300)

                ftp.connect(self.ftp_host, self.ftp_port)
                ftp.login(self.ftp_user, self.ftp_pass)
                return ftp
            except Exception as e:
                if attempt < max_connect_retries - 1:
                    time.sleep((attempt + 1) * 2)
                else:
                    raise

    def _close_connection(self, ftp):
        """Ferme proprement une connexion"""
        if ftp:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    def _is_connection_alive(self, ftp) -> bool:
        """Vérifie si la connexion est encore active"""
        try:
            ftp.voidcmd('NOOP')
            return True
        except Exception:
            return False

    def _reconnect(self, ftp, worker_id: int):
        """Ferme l'ancienne connexion et en crée une nouvelle"""
        self._close_connection(ftp)
        with self.stats_lock:
            self.stats['reconnections'] += 1
        logger.info(f"[Worker {worker_id}] Reconnecting...")
        time.sleep(1)  # Brief pause before reconnect
        return self._create_ftp_connection()

    def _worker(self, worker_id: int):
        """Worker thread qui traite les téléchargements"""
        ftp = None
        consecutive_errors = 0
        max_consecutive_errors = 5
        files_since_health_check = 0
        health_check_interval = 50  # Check connection every N files, not every file

        try:
            ftp = self._create_ftp_connection()

            while not self.stop_flag.is_set():
                try:
                    # Récupérer une tâche (timeout 2 sec pour vérifier stop_flag)
                    priority, task = self.task_queue.get(timeout=2)

                    with self.stats_lock:
                        self.stats['workers_active'] += 1

                    # Periodic health check (not every file — saves round-trips)
                    files_since_health_check += 1
                    if files_since_health_check >= health_check_interval:
                        files_since_health_check = 0
                        if not self._is_connection_alive(ftp):
                            try:
                                ftp = self._reconnect(ftp, worker_id)
                            except Exception as e:
                                logger.warning(f"[Worker {worker_id}] Reconnect failed: {e}")
                                self.task_queue.put((priority, task))
                                with self.stats_lock:
                                    self.stats['workers_active'] -= 1
                                self.task_queue.task_done()
                                time.sleep(5)
                                continue

                    # Télécharger le fichier
                    result = self._download_file(ftp, task, worker_id)

                    # Handle connection errors: reconnect and retry immediately
                    if not result.success and self._is_connection_error(result.error_message):
                        consecutive_errors += 1
                        files_since_health_check = 0  # Reset since we know it's broken
                        try:
                            ftp = self._reconnect(ftp, worker_id)
                            # Retry the download with fresh connection
                            result = self._download_file(ftp, task, worker_id)
                            if result.success:
                                consecutive_errors = 0
                        except Exception:
                            pass
                    elif result.success:
                        consecutive_errors = 0

                    # Envoyer le résultat
                    self.result_queue.put(result)

                    # Retry si échec (requeue)
                    if not result.success and task.retry_count < self.max_retries:
                        task.retry_count += 1
                        self.task_queue.put((priority + 100, task))

                    with self.stats_lock:
                        self.stats['workers_active'] -= 1

                    self.task_queue.task_done()

                    # Too many consecutive errors - reconnect proactively
                    if consecutive_errors >= max_consecutive_errors:
                        try:
                            ftp = self._reconnect(ftp, worker_id)
                            consecutive_errors = 0
                            files_since_health_check = 0
                        except Exception:
                            time.sleep(5)

                except queue.Empty:
                    continue
                except _CONNECTION_ERRORS as e:
                    logger.warning(f"[Worker {worker_id}] Connection error: {e}")
                    files_since_health_check = 0
                    try:
                        ftp = self._reconnect(ftp, worker_id)
                    except Exception:
                        time.sleep(5)
                except Exception as e:
                    logger.warning(f"[Worker {worker_id}] Unexpected error: {e}")
                    continue

        finally:
            self._close_connection(ftp)

    @staticmethod
    def _is_connection_error(error_message: str) -> bool:
        """Détecte si l'erreur est liée à la connexion"""
        if not error_message:
            return False
        msg = error_message.lower()
        indicators = [
            'connection', 'broken pipe', 'reset by peer', 'timed out',
            'eof', 'socket', 'channel closed', 'not connected',
            'transport', 'ssh', 'session', 'closed'
        ]
        return any(ind in msg for ind in indicators)

    def _ensure_local_dir(self, file_path: str):
        """Create parent directory, with caching to avoid repeated syscalls"""
        dir_path = os.path.dirname(file_path)
        if dir_path in self._created_dirs:
            return
        os.makedirs(dir_path, exist_ok=True)
        with self._dirs_lock:
            self._created_dirs.add(dir_path)

    def _download_file(self, ftp, task: DownloadTask, worker_id: int) -> DownloadResult:
        """Télécharge un fichier unique"""
        start_time = time.time()

        try:
            # Créer le dossier local (cached)
            self._ensure_local_dir(task.local_path)

            # Use direct SFTP download if available (bypasses retrbinary overhead)
            if self._is_sftp and hasattr(ftp, 'download_file'):
                ftp.download_file(task.remote_path, task.local_path)
            else:
                with open(task.local_path, 'wb') as f:
                    ftp.retrbinary(f"RETR {task.remote_path}", f.write)

            # Vérification d'intégrité
            if self.verify_integrity and task.size > 0:
                actual_size = os.path.getsize(task.local_path)
                if actual_size != task.size:
                    os.remove(task.local_path)
                    return DownloadResult(
                        rel_path=task.rel_path,
                        success=False,
                        size=0,
                        duration=time.time() - start_time,
                        error_message=f"Size mismatch: expected {task.size}, got {actual_size}",
                        retry_count=task.retry_count
                    )

            # Succès
            duration = time.time() - start_time

            with self.stats_lock:
                self.stats['completed'] += 1
                self.stats['bytes_transferred'] += task.size

            return DownloadResult(
                rel_path=task.rel_path,
                success=True,
                size=task.size,
                duration=duration,
                retry_count=task.retry_count
            )

        except Exception as e:
            # Clean up partial file
            try:
                if os.path.exists(task.local_path):
                    os.remove(task.local_path)
            except OSError:
                pass

            with self.stats_lock:
                self.stats['failed'] += 1

            return DownloadResult(
                rel_path=task.rel_path,
                success=False,
                size=0,
                duration=time.time() - start_time,
                error_message=str(e),
                retry_count=task.retry_count
            )

    def add_tasks(self, tasks: List[DownloadTask]):
        """Ajoute des tâches à la queue"""
        self.stats['total_files'] = len(tasks)

        # Trier par taille (petits fichiers d'abord pour feedback rapide)
        tasks_sorted = sorted(tasks, key=lambda t: (t.priority, t.size))

        for idx, task in enumerate(tasks_sorted):
            self.task_queue.put((idx, task))

    def start(self):
        """Démarre les workers avec démarrage échelonné"""
        self.stats['start_time'] = time.time()
        self.stop_flag.clear()

        # Stagger worker startup: 0.5s between each to avoid
        # hammering the server with simultaneous SSH handshakes
        stagger_delay = 0.5 if self.ftp_port == 22 else 0.1

        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self._worker,
                args=(i,),
                daemon=True,
                name=f"FTPWorker-{i}"
            )
            worker.start()
            self.workers.append(worker)

            # Don't delay after the last worker
            if i < self.num_workers - 1:
                time.sleep(stagger_delay)

    def stop(self):
        """Arrête les workers"""
        self.stop_flag.set()

        for worker in self.workers:
            worker.join(timeout=10)

    def wait_completion(self, progress_callback: Optional[Callable] = None):
        """
        Attend que tous les téléchargements soient terminés

        Args:
            progress_callback: Fonction appelée avec (completed, total, stats)
        """
        last_progress_time = time.time()
        progress_interval = 1.0  # Mise à jour chaque seconde
        stall_timeout = 300  # 5 min without progress = consider stalled
        last_completed = 0
        last_progress_change = time.time()

        while True:
            with self.stats_lock:
                completed = self.stats['completed'] + self.stats['failed']
                total = self.stats['total_files']

            # Track stalling
            if completed > last_completed:
                last_completed = completed
                last_progress_change = time.time()

            # Callback de progression
            if progress_callback and (time.time() - last_progress_time) >= progress_interval:
                progress_callback(completed, total, self.get_statistics())
                last_progress_time = time.time()

            # Vérifier si terminé
            if completed >= total and self.task_queue.empty():
                break

            # Stall detection
            if time.time() - last_progress_change > stall_timeout:
                logger.warning("Download appears stalled, stopping workers...")
                break

            time.sleep(0.2)

        # Dernier callback
        if progress_callback:
            progress_callback(self.stats['completed'] + self.stats['failed'],
                            self.stats['total_files'],
                            self.get_statistics())

    def get_statistics(self) -> Dict:
        """Récupère les statistiques actuelles"""
        with self.stats_lock:
            stats = self.stats.copy()

        if stats['start_time']:
            stats['elapsed_time'] = time.time() - stats['start_time']

            if stats['elapsed_time'] > 0:
                stats['files_per_second'] = stats['completed'] / stats['elapsed_time']
                stats['mbps'] = (stats['bytes_transferred'] / stats['elapsed_time']) / (1024 * 1024)

                # Estimation du temps restant
                if stats['completed'] > 0:
                    total = stats['total_files']
                    rate = stats['completed'] / stats['elapsed_time']
                    remaining = total - stats['completed']
                    stats['estimated_time_remaining'] = remaining / rate if rate > 0 else 0

        return stats

    def collect_results(self) -> List[DownloadResult]:
        """Collecte tous les résultats disponibles"""
        results = []
        while not self.result_queue.empty():
            try:
                results.append(self.result_queue.get_nowait())
            except queue.Empty:
                break
        return results


class DownloadOrganizer:
    """
    Organise les téléchargements de manière intelligente
    """

    @staticmethod
    def prioritize_by_size(tasks: List[DownloadTask], small_first: bool = True) -> List[DownloadTask]:
        """Priorise par taille (petits d'abord pour feedback rapide)"""
        for idx, task in enumerate(sorted(tasks, key=lambda t: t.size, reverse=not small_first)):
            task.priority = idx
        return tasks

    @staticmethod
    def prioritize_by_directory(tasks: List[DownloadTask]) -> List[DownloadTask]:
        """Groupe par répertoire pour optimiser les accès FTP"""
        # Grouper par dossier parent
        by_dir = {}
        for task in tasks:
            dir_path = os.path.dirname(task.rel_path)
            if dir_path not in by_dir:
                by_dir[dir_path] = []
            by_dir[dir_path].append(task)

        # Assigner les priorités par dossier
        priority = 0
        result = []
        for dir_path in sorted(by_dir.keys()):
            for task in by_dir[dir_path]:
                task.priority = priority
                result.append(task)
                priority += 1

        return result

    @staticmethod
    def prioritize_hybrid(tasks: List[DownloadTask]) -> List[DownloadTask]:
        """
        Stratégie hybride :
        - Petits fichiers d'abord (feedback rapide)
        - Groupés par dossier (efficacité FTP)
        """
        # Séparer petits et gros fichiers
        threshold = 1024 * 1024  # 1 MB
        small_files = [t for t in tasks if t.size <= threshold]
        large_files = [t for t in tasks if t.size > threshold]

        # Petits fichiers par dossier
        small_by_dir = DownloadOrganizer.prioritize_by_directory(small_files)

        # Gros fichiers par taille
        large_by_size = DownloadOrganizer.prioritize_by_size(large_files, small_first=False)

        # Combiner : petits d'abord, puis gros
        priority = 0
        for task in small_by_dir:
            task.priority = priority
            priority += 1

        for task in large_by_size:
            task.priority = priority
            priority += 1

        return small_by_dir + large_by_size
