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
        logging.FileHandler(os.path.join(LOG_DIR, 'deploy.log')),
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

def get_local_files(local_root):
    files = {}
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

def ensure_remote_dir(ftp, remote_dir):
    parts = remote_dir.strip('/').split('/')
    current = ''
    for part in parts:
        if not part: continue
        current += '/' + part
        try:
            ftp.cwd(current)
        except:
            logging.info(f"Creating remote directory: {current}")
            ftp.mkd(current)
            ftp.cwd(current)

def upload_file(ftp, local_path, remote_path):
    remote_dir = os.path.dirname(remote_path)
    if remote_dir and remote_dir != '/':
        ensure_remote_dir(ftp, remote_dir)
        
    logging.info(f"Uploading {local_path} to {remote_path}")
    with open(local_path, 'rb') as f:
        ftp.storbinary(f"STOR {remote_path}", f)

def main():
    config = load_config()
    if 'deploy' not in config:
        logging.error("No 'deploy' section found in config.yaml")
        return

    ftp_cfg = config['ftp']
    dep_cfg = config['deploy']
    
    local_root = dep_cfg['local_root']
    remote_root = dep_cfg['remote_root']
    state_file = dep_cfg.get('state_file', './deploy_state.json')
    
    current_state = load_state(state_file)
    new_state = {}
    
    try:
        logging.info(f"Connecting to {ftp_cfg['host']} for deployment...")
        with FTP() as ftp:
            ftp.connect(ftp_cfg['host'], ftp_cfg['port'])
            ftp.login(ftp_cfg['user'], ftp_cfg['password'])
            
            logging.info("Scanning local files...")
            local_files = get_local_files(local_root)
            
            for rel_path, info in local_files.items():
                local_path = os.path.join(local_root, rel_path)
                remote_path = os.path.join(remote_root, rel_path).replace('\\', '/')
                
                # Check if file needs update
                needs_upload = False
                if rel_path not in current_state:
                    needs_upload = True
                else:
                    old_info = current_state[rel_path]
                    if info['size'] != old_info['size'] or info['modify'] != old_info['modify']:
                        needs_upload = True
                
                if needs_upload:
                    upload_file(ftp, local_path, remote_path)
                
                new_state[rel_path] = info
            
            save_state(state_file, new_state)
            logging.info("Deployment completed successfully.")
            
    except Exception as e:
        logging.error(f"Deployment failed: {e}")

if __name__ == "__main__":
    main()
