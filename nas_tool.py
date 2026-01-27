import os
import json
import logging
import yaml
import sys
from ftplib import FTP
from datetime import datetime
from dotenv import load_dotenv

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table
from rich import print as rprint

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
        
        ftp = FTP(timeout=300) # 5 minutes timeout for large listings
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

    def get_remote_files(self, ftp, remote_root, base_path='', status=None, _count=None):
        if _count is None: _count = [0]


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
                        # Directory - recurse
                        files.update(self.get_remote_files(ftp, remote_root, rel_path, status, _count))
                    else:
                        # File
                        files[rel_path] = {
                            'size': int(parts[4]),
                            'modify': '',  # Not available in LIST
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
                    logging.info(f"Attempting to create directory: {current}")
                    ftp.mkd(current)
                    ftp.cwd(current)
                except Exception as e:
                    logging.warning(f"Could not create or enter {current}: {e}. This might be due to permissions.")

    def deploy(self, local_path, remote_project_name):
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_deploy_{remote_project_name}.json"
        
        current_state = self.load_state(state_file)
        
        with console.status("[bold green]Scanning local files...") as status:
            local_files = self.get_local_files(local_path)
            console.print(f"[green]Found {len(local_files)} local files.[/green]")

        new_state = {}

        with self.connect() as ftp:
            self.ensure_remote_dir(ftp, remote_path)
            
            # Calculate total upload size
            files_to_upload = []
            total_bytes = 0
            
            for rel_path, info in local_files.items():
                target_remote = os.path.join(remote_path, rel_path).replace('\\', '/')
                needs_upload = rel_path not in current_state or \
                               current_state[rel_path]['size'] != info['size'] or \
                               current_state[rel_path]['modify'] != info['modify']
                if needs_upload:
                    files_to_upload.append((rel_path, target_remote, info['size']))
                    total_bytes += info['size']
                new_state[rel_path] = info

            if not files_to_upload:
                console.print("[bold green]up-to-date. No files to deploy.[/bold green]")
                self.save_state(state_file, new_state)
                return

            console.print(f"[bold cyan]Starting deployment of {len(files_to_upload)} files ({total_bytes/1024/1024:.2f} MB)...[/bold cyan]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                overall_task = progress.add_task("[green]Total Upload...", total=total_bytes)
                
                for rel_path, target_remote, size in files_to_upload:
                    progress.update(overall_task, description=f"[green]Uploading {rel_path}...")
                    
                    self.ensure_remote_dir(ftp, os.path.dirname(target_remote))
                    try:
                        with open(os.path.join(local_path, rel_path), 'rb') as f:
                            ftp.storbinary(f"STOR {target_remote}", f, callback=lambda data: progress.update(overall_task, advance=len(data)))
                    except Exception as e:
                        console.print(f"[red]Failed to upload {rel_path}: {e}[/red]")
                        logging.error(f"Failed to upload {rel_path}: {e}")
                
        self.save_state(state_file, new_state)
        console.print("[bold green]Deployment finished successfully![/bold green]")


    def backup(self, local_path, remote_project_name):
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_backup_{remote_project_name.replace('/', '_')}.json"
        
        current_state = self.load_state(state_file)
        new_state = {}

        if not os.path.exists(local_path):
            os.makedirs(local_path)

        with self.connect() as ftp:
            console.print(f"[cyan]Connecting to {self.ftp_host}...[/cyan]")
            
            with console.status(f"[bold cyan]Scanning remote project path: {remote_path}...") as status:
                # Get remote files with relative paths, updating status
                remote_files = self.get_remote_files(ftp, remote_path, status=status)
            
            if not remote_files:
                console.print(f"[bold red]No files found in remote project: {remote_path}[/bold red]")
                return

            console.print(f"[green]Found {len(remote_files)} files to backup.[/green]")
            
            # Calculate download size and filter
            files_to_download = []
            total_bytes = 0
            
            for rel_path, info in remote_files.items():
                needs_download = rel_path not in current_state or \
                                current_state[rel_path]['size'] != info['size'] or \
                                current_state[rel_path]['modify'] != info['modify']
                if needs_download:
                    files_to_download.append((rel_path, info['size']))
                    total_bytes += info['size']
                new_state[rel_path] = info

            if not files_to_download:
                console.print("[bold green]Local backup is up-to-date.[/bold green]")
                self.save_state(state_file, new_state)
                return

            console.print(f"[bold cyan]Starting backup of {len(files_to_download)} files ({total_bytes/1024/1024:.2f} MB)...[/bold cyan]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                overall_task = progress.add_task("[blue]Total Download...", total=total_bytes)
                
                for rel_path, size in files_to_download:
                    progress.update(overall_task, description=f"[blue]Downloading {rel_path}...")
                    
                    local_file_path = os.path.join(local_path, rel_path)
                    remote_file_path = os.path.join(remote_path, rel_path).replace('\\', '/')
                    
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    
                    # Retry logic
                    for scan_attempt in range(3):
                        try:
                            # Re-verify connection is alive if needed (rudimentary check handled by retrbinary usually)
                            # But if previous failed, we need to reconnect
                            if scan_attempt > 0:
                                try:
                                    ftp.voidcmd('NOOP')
                                except:
                                    progress.console.log("[yellow]Connection lost. Reconnecting...[/yellow]")
                                    ftp = self.connect()

                            with open(local_file_path, 'wb') as f:
                                ftp.retrbinary(f"RETR {remote_file_path}", 
                                             lambda data: (f.write(data), progress.update(overall_task, advance=len(data)))[0])
                            break
                        except Exception as e:
                            progress.console.log(f"[yellow]Failed to download {rel_path} (Attempt {scan_attempt+1}/3): {e}[/yellow]")
                            if scan_attempt == 2:
                                progress.console.log(f"[bold red]Permanent failure for {rel_path}: {e}[/bold red]")
                
        self.save_state(state_file, new_state)
        console.print(f"[bold green]Backup finished. {len(new_state)} files synced.[/bold green]")

    def load_state(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f: return json.load(f)
        return {}

    def save_state(self, path, state):
        with open(path, 'w') as f: json.dump(state, f, indent=4)

def interactive_menu():
    tool = SynergyTool()
    console.print("\n[bold magenta]--- SYNERGY FTP TOOL ---[/bold magenta]")
    console.print("1. [bold cyan]Deploy[/bold cyan] (Local -> FTP)")
    console.print("2. [bold cyan]Backup[/bold cyan] (FTP -> Local)")
    console.print("3. [bold yellow]Setup .env file[/bold yellow] (Initial configuration)")
    console.print("4. Exit")
    
    choice = console.input("\n[bold]Choice:[/bold] ")
    
    if choice == '3':
        if not os.path.exists('.env'):
            import shutil
            if os.path.exists('.env.example'):
                shutil.copy('.env.example', '.env')
                console.print("\n[bold green]✅ Success: .env file created from template.[/bold green]")
                console.print("👉 Please open the '.env' file and fill in your FTP credentials.")
            else:
                console.print("\n[bold red]❌ Error: .env.example not found.[/bold red]")
        else:
            console.print("\n[bold yellow]💡 Info: .env file already exists. Open it to edit your credentials.[/bold yellow]")
        return

    if choice in ['1', '2']:
        local_dir = console.input("[bold]Local directory (ex: ./project):[/bold] ") or "./project"
        project_name = console.input(f"[bold]Project name on server (default: {os.path.basename(local_dir)}):[/bold] ") or os.path.basename(local_dir)
        
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
