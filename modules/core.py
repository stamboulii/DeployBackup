import os
import json
import logging
import sys
from ftplib import FTP
from datetime import datetime
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from modules.sftp_adapter import SFTPAdapter

# Load environment variables
load_dotenv()

# Setup logging with Rich
LOG_DIR = './logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    datefmt="[%X]",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'nas_tool.log')),
        RichHandler(rich_tracebacks=True)
    ]
)

console = Console()

class SynergyCore:
    DEPLOY_STATE_FILE = '.deploy_enabled'
    PROTECTED_PATHS = ['/', '/ftp', '/production', '/prod', '/live', '/www', '/public_html']
    EXCLUDE_PATTERNS = [
        '*.log', '*.tmp', '.git/', '.svn/',
        'node_modules/', '__pycache__/', '*.pyc',
        'cache/', 'tmp/', 'temp/', '.DS_Store',
        'Thumbs.db', '.idea/', '.vscode/',
        '.sessions/', 'sessions/', 'sess_',
    ]
    
    def __init__(self):
        self.ftp_host = os.getenv('FTP_HOST')
        self.ftp_port = int(os.getenv('FTP_PORT', 21))
        self.ftp_user = os.getenv('FTP_USER')
        self.ftp_pass = os.getenv('FTP_PASSWORD')
        self.remote_base = os.getenv('FTP_REMOTE_ROOT', '/')

    def connect(self):
        if not all([self.ftp_host, self.ftp_user, self.ftp_pass]):
            logging.error("Missing FTP credentials in .env file.")
            sys.exit(1)
        
        if self.ftp_port == 22:
            ftp = SFTPAdapter(timeout=300)
        else:
            ftp = FTP(timeout=300)
            
        ftp.connect(self.ftp_host, self.ftp_port)
        ftp.login(self.ftp_user, self.ftp_pass)
        return ftp

    def get_local_files(self, local_root):
        files = {}
        if not os.path.exists(local_root):
            return files
        for root, dirs, filenames in os.walk(local_root):
            for name in filenames:
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, local_root).replace('\\', '/')
                stat = os.stat(full_path)
                files[rel_path] = {
                    'size': stat.st_size,
                    'modify': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
        return files

    def get_remote_files(self, ftp, remote_root, base_path=None, status=None, _count=None):
        if _count is None: _count = [0]
        if base_path is None: base_path = ''

        files = {}
        current_path = os.path.join(remote_root, base_path).replace('\\', '/')
        
        try:
            ftp.cwd(current_path)
            items = []
            
            # Try MLSD first (more reliable)
            try:
                ftp.retrlines('MLSD', items.append)
                
                for item in items:
                    parts = item.split(';')
                    name = parts[-1].strip()
                    
                    if name in ('.', '..'):
                        continue
                    
                    props = {p.split('=')[0]: p.split('=')[1] for p in parts[:-1] if '=' in p}
                    rel_path = os.path.join(base_path, name).replace('\\', '/')
                    
                    if props.get('type') == 'dir':
                        files.update(self.get_remote_files(ftp, remote_root, rel_path, status, _count))
                    elif props.get('type') == 'file':
                        files[rel_path] = {
                            'size': int(props.get('size', 0)),
                            'modify': props.get('modify', ''),
                        }
                        _count[0] += 1
                        if status and _count[0] % 10 == 0:
                            status.update(f"[bold cyan]Scanning... found {_count[0]} files so far")
            except:
                # Fallback to LIST/DIR if MLSD not supported
                items = []
                ftp.dir(items.append)
                
                for item in items:
                    parts = item.split(None, 8)
                    if len(parts) < 9:
                        continue
                    
                    permissions = parts[0]
                    name = parts[8]
                    
                    if name in ('.', '..'):
                        continue
                    
                    rel_path = os.path.join(base_path, name).replace('\\', '/')
                    
                    if permissions.startswith('d'):
                        files.update(self.get_remote_files(ftp, remote_root, rel_path, status, _count))
                    else:
                        files[rel_path] = {
                            'size': int(parts[4]),
                            'modify': '',
                        }
                        _count[0] += 1
                        if status and _count[0] % 10 == 0:
                            status.update(f"[bold cyan]Scanning... found {_count[0]} files so far")
                        
        except Exception as e:
            logging.error(f"Error scanning {current_path}: {e}")
        
        return files

    def ensure_remote_dir(self, ftp, remote_dir):
        if not remote_dir or remote_dir == '/':
            return
            
        parts = remote_dir.strip('/').split('/')
        current = ''
        for part in parts:
            if not part: continue
            current += '/' + part
            try:
                ftp.cwd(current)
            except Exception:
                try:
                    logging.info(f"Creating directory: {current}")
                    ftp.mkd(current)
                    ftp.cwd(current)
                except Exception as e:
                    logging.warning(f"Could not create or enter {current}: {e}")

    def is_protected_path(self, remote_path):
        """Vérifie si le chemin est protégé"""
        normalized = remote_path.rstrip('/')
        return any(normalized == protected.rstrip('/') for protected in self.PROTECTED_PATHS)
    
    def should_exclude(self, file_path):
        """Vérifie si un fichier doit être exclu du backup"""
        for pattern in self.EXCLUDE_PATTERNS:
            if pattern.endswith('/'):
                # Dossier
                if pattern.rstrip('/') in file_path.split('/'):
                    return True
            elif pattern.startswith('*.'):
                # Extension
                if file_path.endswith(pattern[1:]):
                    return True
            elif pattern in file_path:
                # Pattern exact
                return True
        return False
    
    def verify_file_integrity(self, local_file, expected_size):
        """Vérifie l'intégrité du fichier téléchargé avec tolérance"""
        if not os.path.exists(local_file):
            return False, "File doesn't exist"
        
        actual_size = os.path.getsize(local_file)
        # Tolérance de 0.1% pour les différences de taille dues à l'encodage/transfert
        tolerance = max(int(expected_size * 0.001), 10)  # 0.1% ou minimum 10 bytes
        if abs(actual_size - expected_size) > tolerance:
            return False, f"Size mismatch: expected {expected_size}, got {actual_size} (tolerance: {tolerance})"
        
        return True, "OK"

    def load_state(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f: 
                return json.load(f)
        return {}

    def save_state(self, path, state):
        with open(path, 'w') as f: 
            json.dump(state, f, indent=4)
