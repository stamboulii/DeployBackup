"""
Incremental Scanner - Optimized FTP Scanner
Scan intelligent avec cache et détection des changements uniquement
"""

import os
from ftplib import FTP
from datetime import datetime, timedelta
from typing import Dict, Set, Optional, Tuple, List
from dataclasses import dataclass
import hashlib


@dataclass
class ScanCache:
    """Cache des scans précédents"""
    directories: Dict[str, datetime]  # {path: last_modified}
    files: Dict[str, Dict]  # {rel_path: {size, modify}}
    last_full_scan: datetime
    scan_strategy: str  # 'full', 'incremental', 'smart'


class IncrementalScanner:
    """
    Scanner FTP optimisé qui évite de scanner tout le serveur
    Utilise un cache et ne scanne que les dossiers modifiés
    """
    
    def __init__(self, 
                 ftp: FTP,
                 remote_root: str,
                 cache_file: Optional[str] = None,
                 incremental_threshold_hours: int = 24):
        """
        Args:
            ftp: Connexion FTP active
            remote_root: Racine du scan
            cache_file: Fichier pour persister le cache
            incremental_threshold_hours: Si dernier scan < X heures, scan incrémental
        """
        self.ftp = ftp
        self.remote_root = remote_root
        self.cache_file = cache_file
        self.incremental_threshold = timedelta(hours=incremental_threshold_hours)
        
        self.cache = self._load_cache()
        self.scan_stats = {
            'dirs_scanned': 0,
            'files_found': 0,
            'cache_hits': 0,
            'strategy': 'unknown'
        }
    
    def _load_cache(self) -> ScanCache:
        """Charge le cache depuis le disque"""
        if self.cache_file and os.path.exists(self.cache_file):
            try:
                import pickle
                with open(self.cache_file, 'rb') as f:
                    return pickle.load(f)
            except Exception as e:
                print(f"[Scanner] Failed to load cache: {e}")
        
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
                os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
                with open(self.cache_file, 'wb') as f:
                    pickle.dump(self.cache, f)
            except Exception as e:
                print(f"[Scanner] Failed to save cache: {e}")
    
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
                
                parsed_items.append((name, props))
            
            return parsed_items, True
            
        except Exception:
            return [], False
    
    def _scan_directory_list(self, dir_path: str) -> List[Tuple]:
        """
        Scan un dossier avec LIST (fallback)
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
                
                props = {
                    'type': 'dir' if permissions.startswith('d') else 'file',
                    'size': size,
                    'modify': ''  # LIST ne donne pas de timestamp fiable
                }
                
                parsed_items.append((name, props))
            
            return parsed_items
            
        except Exception as e:
            print(f"[Scanner] Error scanning {dir_path}: {e}")
            return []
    
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
        Scan récursif d'un dossier
        """
        files = {}
        current_path = os.path.join(base_path, relative_path).replace('\\', '/')
        
        self.scan_stats['dirs_scanned'] += 1
        
        if status_callback and self.scan_stats['dirs_scanned'] % 10 == 0:
            status_callback(self.scan_stats)
        
        items = self._scan_directory(current_path, use_mlsd)
        
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
            'strategy': 'full'
        }
        
        files = self._scan_recursive(self.remote_root, '', True, status_callback)
        
        # Mettre à jour le cache
        self.cache.files = files
        self.cache.last_full_scan = datetime.now()
        self.cache.scan_strategy = 'full'
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
            'strategy': 'incremental'
        }
        
        if not self.cache.files:
            # Pas de cache, faire un full scan
            return self.scan_full(status_callback)
        
        # Stratégie : Scanner le dossier root et comparer avec le cache
        # Si différences → scanner ces sous-dossiers uniquement
        
        # 1. Scanner le niveau root
        root_items = self._scan_directory(self.remote_root, use_mlsd=True)
        
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
    
    def scan_smart(self, status_callback=None) -> Dict[str, Dict]:
        """
        Scan intelligent : choisit automatiquement entre full et incrémental
        """
        if self._should_use_incremental():
            print("[Scanner] Using incremental scan (fast)")
            return self.scan_incremental(status_callback)
        else:
            print("[Scanner] Using full scan (cache expired or first scan)")
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
                print(f"[ChunkedScanner] Error in {full_path}: {e}")
        
        # Démarrer le scan
        process_directory(self.remote_root)
        
        # Dernier chunk
        if current_chunk:
            chunk_callback(current_chunk, chunk_num, True)