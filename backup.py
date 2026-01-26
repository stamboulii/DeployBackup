import os
import json
import logging
import yaml
from ftplib import FTP
from datetime import datetime

# Setup logging
LOG_DIR = './logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'backup.log')),
        logging.StreamHandler()
    ]
)

def load_config(config_path='config.yaml'):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {}

def save_state(state_file, state):
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=4)

def get_ftp_files(ftp, remote_path='/'):
    files = {}
    try:
        ftp.cwd(remote_path)
        items = []
        ftp.retrlines('MLSD', items.append)
        
        for item in items:
            parts = item.split(';')
            name = parts[-1].strip()
            props = {p.split('=')[0]: p.split('=')[1] for p in parts[:-1] if '=' in p}
            
            full_path = os.path.join(remote_path, name).replace('\\', '/')
            
            if props.get('type') == 'dir':
                # Skip current and parent dirs if they appear (MLSD usually doesn't show them)
                if name in ('.', '..'): continue
                files.update(get_ftp_files(ftp, full_path))
            elif props.get('type') == 'file':
                files[full_path] = {
                    'size': int(props.get('size', 0)),
                    'modify': props.get('modify', ''),
                }
    except Exception as e:
        logging.error(f"Error scanning {remote_path}: {e}")
    return files

def download_file(ftp, remote_path, local_path):
    local_dir = os.path.dirname(local_path)
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)
        
    logging.info(f"Downloading {remote_path} to {local_path}")
    with open(local_path, 'wb') as f:
        ftp.retrbinary(f"RETR {remote_path}", f.write)

def main():
    config = load_config()
    ftp_cfg = config['ftp']
    bak_cfg = config['backup']
    
    local_root = bak_cfg['local_root']
    state_file = bak_cfg['state_file']
    
    current_state = load_state(state_file)
    new_state = {}
    
    try:
        logging.info(f"Connecting to {ftp_cfg['host']}...")
        with FTP() as ftp:
            ftp.connect(ftp_cfg['host'], ftp_cfg['port'])
            ftp.login(ftp_cfg['user'], ftp_cfg['password'])
            
            logging.info("Scanning remote files...")
            remote_files = get_ftp_files(ftp, ftp_cfg['remote_root'])
            
            for path, info in remote_files.items():
                # Relative path for local storage
                rel_path = os.path.relpath(path, ftp_cfg['remote_root'])
                local_path = os.path.join(local_root, rel_path)
                
                # Check if file needs update
                needs_download = False
                if path not in current_state:
                    needs_download = True
                else:
                    old_info = current_state[path]
                    if info['size'] != old_info['size'] or info['modify'] != old_info['modify']:
                        needs_download = True
                
                if needs_download:
                    download_file(ftp, path, local_path)
                
                new_state[path] = info
            
            save_state(state_file, new_state)
            logging.info("Backup completed successfully.")
            
    except Exception as e:
        logging.error(f"Backup failed: {e}")

if __name__ == "__main__":
    main()
