"""
State Manager - SQLite Database for Efficient File State Management
Replaces JSON files with SQLite for 1M+ files support
"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, List, Set, Optional, Tuple


class StateManager:
    """
    Gère l'état des fichiers dans une base SQLite au lieu de JSON
    Optimisé pour 1M+ fichiers avec index et requêtes efficaces
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialise la base de données avec les tables et index nécessaires"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table principale pour l'état des fichiers
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS file_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT UNIQUE NOT NULL,
                    size INTEGER NOT NULL,
                    modify TEXT NOT NULL,
                    checksum TEXT,
                    last_sync TEXT NOT NULL,
                    status TEXT DEFAULT 'synced'
                )
            ''')
            
            # Table pour les checkpoints (reprise après crash)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    files_processed INTEGER NOT NULL,
                    files_total INTEGER NOT NULL,
                    bytes_transferred INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
            ''')
            
            # Table pour les logs d'erreurs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0
                )
            ''')
            
            # Index pour les recherches rapides
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rel_path ON file_state(rel_path)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON file_state(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_id ON sync_checkpoints(sync_id)')
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Context manager pour les connexions SQLite"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_file_state(self, rel_path: str) -> Optional[Dict]:
        """Récupère l'état d'un fichier spécifique"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT rel_path, size, modify, checksum, last_sync FROM file_state WHERE rel_path = ?',
                (rel_path,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def get_all_files(self) -> Dict[str, Dict]:
        """Récupère tous les fichiers (optimisé avec streaming)"""
        files = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT rel_path, size, modify, checksum FROM file_state')
            
            # Streaming par batch de 10,000
            while True:
                rows = cursor.fetchmany(10000)
                if not rows:
                    break
                
                for row in rows:
                    files[row['rel_path']] = {
                        'size': row['size'],
                        'modify': row['modify'],
                        'checksum': row['checksum']
                    }
        
        return files
    
    def get_files_set(self) -> Set[str]:
        """Récupère uniquement les chemins de fichiers (très rapide)"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT rel_path FROM file_state')
            return {row['rel_path'] for row in cursor.fetchall()}
    
    def update_file_batch(self, files: Dict[str, Dict], batch_size: int = 1000):
        """
        Met à jour plusieurs fichiers en batch (beaucoup plus rapide)
        
        Args:
            files: Dict avec {rel_path: {'size': ..., 'modify': ...}}
            batch_size: Nombre de fichiers par transaction
        """
        timestamp = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            file_list = list(files.items())
            total_batches = (len(file_list) + batch_size - 1) // batch_size
            
            for batch_num in range(total_batches):
                start_idx = batch_num * batch_size
                end_idx = min(start_idx + batch_size, len(file_list))
                batch = file_list[start_idx:end_idx]
                
                # UPSERT batch
                cursor.executemany('''
                    INSERT INTO file_state (rel_path, size, modify, last_sync, status)
                    VALUES (?, ?, ?, ?, 'synced')
                    ON CONFLICT(rel_path) DO UPDATE SET
                        size = excluded.size,
                        modify = excluded.modify,
                        last_sync = excluded.last_sync,
                        status = 'synced'
                ''', [
                    (rel_path, info['size'], info['modify'], timestamp)
                    for rel_path, info in batch
                ])
                
                conn.commit()
    
    def delete_files(self, rel_paths: List[str], batch_size: int = 1000):
        """Supprime plusieurs fichiers en batch"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            for i in range(0, len(rel_paths), batch_size):
                batch = rel_paths[i:i+batch_size]
                placeholders = ','.join('?' * len(batch))
                cursor.execute(
                    f'DELETE FROM file_state WHERE rel_path IN ({placeholders})',
                    batch
                )
                conn.commit()
    
    def create_checkpoint(self, sync_id: str, files_processed: int, 
                         files_total: int, bytes_transferred: int, status: str = 'in_progress'):
        """Crée un checkpoint pour la reprise"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sync_checkpoints 
                (sync_id, timestamp, files_processed, files_total, bytes_transferred, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (sync_id, datetime.now().isoformat(), files_processed, 
                  files_total, bytes_transferred, status))
            conn.commit()
    
    def get_last_checkpoint(self, sync_id: str) -> Optional[Dict]:
        """Récupère le dernier checkpoint pour reprendre"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM sync_checkpoints 
                WHERE sync_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 1
            ''', (sync_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def log_error(self, sync_id: str, rel_path: str, error_message: str, retry_count: int = 0):
        """Log une erreur de synchronisation"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sync_errors (sync_id, timestamp, rel_path, error_message, retry_count)
                VALUES (?, ?, ?, ?, ?)
            ''', (sync_id, datetime.now().isoformat(), rel_path, error_message, retry_count))
            conn.commit()
    
    def get_errors(self, sync_id: str) -> List[Dict]:
        """Récupère toutes les erreurs d'une synchro"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM sync_errors WHERE sync_id = ? ORDER BY timestamp',
                (sync_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_statistics(self) -> Dict:
        """Récupère des statistiques sur la base"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Nombre total de fichiers
            cursor.execute('SELECT COUNT(*) as count FROM file_state')
            total_files = cursor.fetchone()['count']
            
            # Taille totale
            cursor.execute('SELECT SUM(size) as total FROM file_state')
            total_size = cursor.fetchone()['total'] or 0
            
            # Dernier sync
            cursor.execute('SELECT MAX(last_sync) as last FROM file_state')
            last_sync = cursor.fetchone()['last']
            
            # Taille de la base
            db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            
            return {
                'total_files': total_files,
                'total_size_bytes': total_size,
                'total_size_mb': total_size / (1024 * 1024),
                'last_sync': last_sync,
                'database_size_mb': db_size / (1024 * 1024)
            }
    
    def vacuum(self):
        """Optimise la base de données (après suppressions massives)"""
        with self._get_connection() as conn:
            conn.execute('VACUUM')
    
    def export_to_json(self, output_path: str):
        """Exporte vers JSON pour migration/backup"""
        import json
        
        files = self.get_all_files()
        with open(output_path, 'w') as f:
            json.dump(files, f, indent=2)
    
    def import_from_json(self, json_path: str):
        """Importe depuis un ancien state JSON"""
        import json
        
        with open(json_path, 'r') as f:
            files = json.load(f)
        
        self.update_file_batch(files)