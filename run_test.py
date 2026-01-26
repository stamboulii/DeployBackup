import subprocess
import time
import os
import yaml
import shutil

CONFIG_FILE = 'config.yaml'
BACKUP_DIR = './data'
PROJECT_DIR = './project'
STATE_FILE_BACKUP = './state.json'
STATE_FILE_DEPLOY = './deploy_state.json'
MOCK_FTP_ROOT = './mock_ftp_root'
PYTHON_EXE = r"C:\Users\MSI\scoop\apps\python\current\python.exe"

def update_config_for_test():
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    
    config['ftp']['host'] = '127.0.0.1'
    config['ftp']['port'] = 2121
    config['ftp']['user'] = 'testuser'
    config['ftp']['password'] = 'testpassword'
    config['ftp']['remote_root'] = '/'
    
    config['backup']['local_root'] = BACKUP_DIR
    config['backup']['state_file'] = STATE_FILE_BACKUP
    
    config['deploy']['local_root'] = PROJECT_DIR
    config['deploy']['remote_root'] = '/'
    config['deploy']['state_file'] = STATE_FILE_DEPLOY
    
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f)

def cleanup():
    for path in [BACKUP_DIR, PROJECT_DIR, STATE_FILE_BACKUP, STATE_FILE_DEPLOY, MOCK_FTP_ROOT, './logs']:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    os.makedirs('./logs')
    os.makedirs(PROJECT_DIR)

def run_test():
    print("--- Starting Deploy & Backup Test ---")
    cleanup()
    update_config_for_test()
    
    # Create initial project files
    with open(os.path.join(PROJECT_DIR, 'main.js'), 'w') as f:
        f.write("console.log('Hello World');")
    
    # Start mock FTP server
    server_proc = subprocess.Popen([PYTHON_EXE, 'test_ftp_server.py'])
    time.sleep(2) # Wait for server to start
    
    try:
        print("\nStep 1: Running deployment (Local -> FTP)...")
        subprocess.run([PYTHON_EXE, 'deploy.py'], check=True)
        
        # Verify files on FTP
        ftp_files = os.listdir(MOCK_FTP_ROOT)
        print(f"Files on FTP: {ftp_files}")
        if 'main.js' in ftp_files:
            print("Deployment SUCCESS")
        else:
            print("Deployment FAILED")
            return
            
        print("\nStep 2: Running backup (FTP -> Local)...")
        subprocess.run([PYTHON_EXE, 'backup.py'], check=True)
        
        # Verify backup
        backup_files = os.listdir(BACKUP_DIR)
        print(f"Files in backup dir: {backup_files}")
        if 'main.js' in backup_files:
            print("Backup SUCCESS")
        else:
            print("Backup FAILED")
            return

        print("\nStep 3: Modifying local project and re-deploying...")
        with open(os.path.join(PROJECT_DIR, 'main.js'), 'a') as f:
            f.write("\n// New line")
        
        subprocess.run([PYTHON_EXE, 'deploy.py'], check=True)
        
        print("\nStep 4: Running incremental backup...")
        subprocess.run([PYTHON_EXE, 'backup.py'], check=True)
        
        with open(os.path.join(BACKUP_DIR, 'main.js'), 'r') as f:
            content = f.read()
            if "// New line" in content:
                print("Incremental cycle SUCCESS")
            else:
                print("Incremental cycle FAILED")
            
    finally:
        server_proc.terminate()
        server_proc.wait()
        print("\n--- Test Finished ---")

if __name__ == "__main__":
    run_test()
