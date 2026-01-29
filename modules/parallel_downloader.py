"""
Parallel Downloader - Multi-threaded FTP Download Manager
Optimisé pour télécharger des milliers de fichiers en parallèle
"""

import os
import queue
import threading
from ftplib import FTP
from dataclasses import dataclass
from typing import List, Callable, Optional, Dict
from datetime import datetime
import time


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
            num_workers: Nombre de connexions FTP simultanées (10-20 recommandé)
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
            'workers_active': 0
        }
        self.stats_lock = threading.Lock()
        
        # Control
        self.stop_flag = threading.Event()
        self.workers = []
    
    def _create_ftp_connection(self) -> FTP:
        """Crée une nouvelle connexion FTP"""
        ftp = FTP(timeout=300)
        ftp.connect(self.ftp_host, self.ftp_port)
        ftp.login(self.ftp_user, self.ftp_pass)
        return ftp
    
    def _worker(self, worker_id: int):
        """Worker thread qui traite les téléchargements"""
        ftp = None
        
        try:
            ftp = self._create_ftp_connection()
            
            while not self.stop_flag.is_set():
                try:
                    # Récupérer une tâche (timeout 1 sec pour vérifier stop_flag)
                    priority, task = self.task_queue.get(timeout=1)
                    
                    with self.stats_lock:
                        self.stats['workers_active'] += 1
                    
                    # Télécharger le fichier
                    result = self._download_file(ftp, task, worker_id)
                    
                    # Envoyer le résultat
                    self.result_queue.put(result)
                    
                    # Retry si échec
                    if not result.success and task.retry_count < self.max_retries:
                        task.retry_count += 1
                        # Remettre dans la queue avec priorité plus basse
                        self.task_queue.put((priority + 100, task))
                    
                    with self.stats_lock:
                        self.stats['workers_active'] -= 1
                    
                    self.task_queue.task_done()
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[Worker {worker_id}] Unexpected error: {e}")
                    continue
        
        finally:
            if ftp:
                try:
                    ftp.quit()
                except:
                    pass
    
    def _download_file(self, ftp: FTP, task: DownloadTask, worker_id: int) -> DownloadResult:
        """Télécharge un fichier unique"""
        start_time = time.time()
        
        try:
            # Créer le dossier local si nécessaire
            os.makedirs(os.path.dirname(task.local_path), exist_ok=True)
            
            # Télécharger
            with open(task.local_path, 'wb') as f:
                ftp.retrbinary(f"RETR {task.remote_path}", f.write)
            
            # Vérification d'intégrité
            if self.verify_integrity:
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
        """Démarre les workers"""
        self.stats['start_time'] = time.time()
        self.stop_flag.clear()
        
        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self._worker,
                args=(i,),
                daemon=True,
                name=f"FTPWorker-{i}"
            )
            worker.start()
            self.workers.append(worker)
    
    def stop(self):
        """Arrête les workers"""
        self.stop_flag.set()
        
        for worker in self.workers:
            worker.join(timeout=5)
    
    def wait_completion(self, progress_callback: Optional[Callable] = None):
        """
        Attend que tous les téléchargements soient terminés
        
        Args:
            progress_callback: Fonction appelée avec (completed, total, stats)
        """
        last_progress_time = time.time()
        progress_interval = 1.0  # Mise à jour chaque seconde
        
        while True:
            completed = self.stats['completed'] + self.stats['failed']
            total = self.stats['total_files']
            
            # Callback de progression
            if progress_callback and (time.time() - last_progress_time) >= progress_interval:
                progress_callback(completed, total, self.get_statistics())
                last_progress_time = time.time()
            
            # Vérifier si terminé
            if completed >= total and self.task_queue.empty():
                break
            
            time.sleep(0.1)
        
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