import os
import json
import logging
import yaml
import sys
from ftplib import FTP
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
LOG_DIR = './logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'nas_tool.log')),
        logging.StreamHandler()
    ]
)

class SynergyTool:
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
        
        ftp = FTP()
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

    def get_remote_files(self, ftp, remote_root, base_path=''):

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
                        # Recursively scan subdirectory
                        files.update(self.get_remote_files(ftp, remote_root, rel_path))
                    elif props.get('type') == 'file':
                        files[rel_path] = {
                            'size': int(props.get('size', 0)),
                            'modify': props.get('modify', ''),
                        }
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
                        # Directory - recurse
                        files.update(self.get_remote_files(ftp, remote_root, rel_path))
                    else:
                        # File
                        files[rel_path] = {
                            'size': int(parts[4]),
                            'modify': '',  # Not available in LIST
                        }
                        
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
                    logging.info(f"Attempting to create directory: {current}")
                    ftp.mkd(current)
                    ftp.cwd(current)
                except Exception as e:
                    logging.warning(f"Could not create or enter {current}: {e}. This might be due to permissions.")

    def deploy(self, local_path, remote_project_name):
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_deploy_{remote_project_name}.json"
        
        current_state = self.load_state(state_file)
        local_files = self.get_local_files(local_path)
        new_state = {}

        with self.connect() as ftp:
            self.ensure_remote_dir(ftp, remote_path)
            for rel_path, info in local_files.items():
                target_remote = os.path.join(remote_path, rel_path).replace('\\', '/')
                needs_upload = rel_path not in current_state or \
                               current_state[rel_path]['size'] != info['size'] or \
                               current_state[rel_path]['modify'] != info['modify']
                
                if needs_upload:
                    self.ensure_remote_dir(ftp, os.path.dirname(target_remote))
                    try:
                        with open(os.path.join(local_path, rel_path), 'rb') as f:
                            ftp.storbinary(f"STOR {target_remote}", f)
                        logging.info(f"Uploaded: {rel_path}")
                    except Exception as e:
                        logging.error(f"Failed to upload {rel_path}: {e}")
                        logging.error("👉 Hint: Check your FTP_REMOTE_ROOT in .env. Does this folder exist and do you have write permissions?")
                        logging.error(f"   Target path was: {target_remote}")
                
                new_state[rel_path] = info
        self.save_state(state_file, new_state)
        logging.info("Deployment finished.")

    # def backup(self, local_path, remote_project_name):
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_backup_{remote_project_name}.json"
        
        current_state = self.load_state(state_file)
        new_state = {}

        if not os.path.exists(local_path):
            os.makedirs(local_path)

        with self.connect() as ftp:
            logging.info(f"Scanning remote project path: {remote_path}")
            remote_files = self.get_remote_files(ftp, remote_path)
            
            if not remote_files:
                logging.warning(f"No files found in remote project: {remote_path}")
                logging.info("listing accessible folders at root for verification:")
                try:
                    ftp.cwd(self.remote_base)
                    ftp.retrlines('LIST')
                except Exception as e:
                    logging.warning(f"Could not list root: {e}")
                return

            for r_path, info in remote_files.items():
                rel_path = os.path.relpath(r_path, remote_path).replace('\\', '/')
                l_path = os.path.join(local_path, rel_path)
                
                needs_download = r_path not in current_state or \
                                 current_state[r_path]['size'] != info['size'] or \
                                 current_state[r_path]['modify'] != info['modify']
                
                if needs_download:
                    os.makedirs(os.path.dirname(l_path), exist_ok=True)
                    with open(l_path, 'wb') as f:
                        ftp.retrbinary(f"RETR {r_path}", f.write)
                    logging.info(f"Downloaded: {rel_path}")
                
                new_state[r_path] = info
        self.save_state(state_file, new_state)
        logging.info("Backup finished.")
    def backup(self, local_path, remote_project_name):
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_backup_{remote_project_name.replace('/', '_')}.json"
        
        current_state = self.load_state(state_file)
        new_state = {}

        if not os.path.exists(local_path):
            os.makedirs(local_path)

        with self.connect() as ftp:
            logging.info(f"Scanning remote project path: {remote_path}")
            
            # Get remote files with relative paths
            remote_files = self.get_remote_files(ftp, remote_path)
            
            if not remote_files:
                logging.warning(f"No files found in remote project: {remote_path}")
                logging.info("Listing accessible folders at root for verification:")
                try:
                    ftp.cwd(self.remote_base if self.remote_base else '/')
                    ftp.retrlines('LIST')
                except Exception as e:
                    logging.warning(f"Could not list root: {e}")
                return

            logging.info(f"Found {len(remote_files)} files to backup")
            
            for rel_path, info in remote_files.items():
                # rel_path is already relative to remote_path
                local_file_path = os.path.join(local_path, rel_path)
                remote_file_path = os.path.join(remote_path, rel_path).replace('\\', '/')
                
                needs_download = rel_path not in current_state or \
                                current_state[rel_path]['size'] != info['size'] or \
                                current_state[rel_path]['modify'] != info['modify']
                
                if needs_download:
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    try:
                        with open(local_file_path, 'wb') as f:
                            ftp.retrbinary(f"RETR {remote_file_path}", f.write)
                        logging.info(f"Downloaded: {rel_path}")
                    except Exception as e:
                        logging.error(f"Failed to download {rel_path}: {e}")
                
                new_state[rel_path] = info
                
        self.save_state(state_file, new_state)
        logging.info(f"Backup finished. {len(new_state)} files in state.")

    def load_state(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f: return json.load(f)
        return {}

    def save_state(self, path, state):
        with open(path, 'w') as f: json.dump(state, f, indent=4)

def interactive_menu():
    tool = SynergyTool()
    print("\n--- SYNERGY FTP TOOL ---")
    print("1. Deploy (Local -> FTP)")
    print("2. Backup (FTP -> Local)")
    print("3. Setup .env file (Initial configuration)")
    print("4. Exit")
    
    choice = input("\nChoice: ")
    
    if choice == '3':
        if not os.path.exists('.env'):
            import shutil
            if os.path.exists('.env.example'):
                shutil.copy('.env.example', '.env')
                print("\n✅ Success: .env file created from template.")
                print("👉 Please open the '.env' file and fill in your FTP credentials.")
            else:
                print("\n❌ Error: .env.example not found.")
        else:
            print("\n💡 Info: .env file already exists. Open it to edit your credentials.")
        return

    if choice in ['1', '2']:
        local_dir = input("Local directory (ex: ./project): ") or "./project"
        project_name = input(f"Project name on server (default: {os.path.basename(local_dir)}): ") or os.path.basename(local_dir)
        
        if choice == '1':
            tool.deploy(local_dir, project_name)
        else:
            tool.backup(local_dir, project_name)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI arguments could be added here for scripting
        pass
    else:
        interactive_menu()
