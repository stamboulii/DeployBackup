import os
import json
from datetime import datetime
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TransferSpeedColumn, TimeRemainingColumn

from modules.core import console

class BackupMixin:
    def handle_deleted_files(self, local_path, deleted_files):
        """
        Supprime automatiquement les fichiers locaux qui ont été
        supprimés sur le serveur distant (mirror sync).
        """
        if not deleted_files:
            return

        deleted_count = 0
        failed_count = 0
        for rel_path in deleted_files:
            local_file = os.path.join(local_path, rel_path)
            if os.path.exists(local_file):
                try:
                    os.remove(local_file)
                    deleted_count += 1
                except Exception as e:
                    failed_count += 1
                    console.print(f"[red]Failed to delete {rel_path}: {e}[/red]")

        console.print(f"[green]✅ Deleted {deleted_count:,} files locally.[/green]")
        if failed_count > 0:
            console.print(f"[yellow]   {failed_count:,} files could not be deleted.[/yellow]")
    
    def backup(self, local_path, remote_project_name, options=None):
        """
        Backup avancé avec toutes les améliorations
        """
        if options is None:
            options = {
                'exclude_patterns': True,
                'verify_integrity': True,
                'handle_deletions': True,
                'parallel_downloads': 0,
            }
        
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        state_file = f"state_backup_{remote_project_name.replace('/', '_')}.json"
        
        current_state = self.load_state(state_file)
        new_state = {}
        
        if not os.path.exists(local_path):
            os.makedirs(local_path)

        with self.connect() as ftp:
            console.print(f"[cyan]Connecting to {self.ftp_host}...[/cyan]")
            
            # Scan remote
            with console.status(f"[bold cyan]Scanning remote: {remote_path}...") as status:
                remote_files = self.get_remote_files(ftp, remote_path, status=status)
            
            if not remote_files:
                console.print(f"[bold red]No files found in remote project: {remote_path}[/bold red]")
                return
            
            # Appliquer les exclusions
            if options.get('exclude_patterns'):
                original_count = len(remote_files)
                remote_files = {
                    k: v for k, v in remote_files.items() 
                    if not self.should_exclude(k)
                }
                excluded_count = original_count - len(remote_files)
                if excluded_count > 0:
                    console.print(f"[yellow]📦 Excluded {excluded_count} files (logs, cache, tmp, etc.)[/yellow]")
            
            console.print(f"[green]✅ Found {len(remote_files)} files to backup.[/green]")
            
            # Calculer les fichiers à télécharger
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
            
            # Gérer les fichiers supprimés
            if options.get('handle_deletions'):
                local_files_in_state = set(current_state.keys())
                remote_files_set = set(remote_files.keys())
                deleted_files = local_files_in_state - remote_files_set
                
                if deleted_files:
                    self.handle_deleted_files(local_path, deleted_files)
                    # Mettre à jour le state pour retirer les fichiers supprimés
                    for deleted_file in deleted_files:
                        if deleted_file in current_state:
                            del current_state[deleted_file]
            
            if not files_to_download:
                console.print("[bold green]✅ Local backup is up-to-date. No files to download.[/bold green]")
                self.save_state(state_file, new_state)
                return
            
            console.print(f"\n[bold cyan]🚀 Starting backup of {len(files_to_download)} files ({total_bytes/1024/1024:.2f} MB)...[/bold cyan]\n")
            
            # Download avec vérification d'intégrité
            success_count, failed_count, corrupted_count = self._download_files(
                ftp, files_to_download, remote_path, 
                local_path, total_bytes, options
            )
        
        self.save_state(state_file, new_state)
        
        # Résumé final
        console.print("\n" + "="*70)
        summary_table = Table(title="📊 Backup Summary", show_header=True, header_style="bold magenta")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Value", style="green", justify="right")
        
        summary_table.add_row("Total Files", str(len(new_state)))
        summary_table.add_row("Files Downloaded", f"[green]{success_count}[/green]")
        if failed_count > 0:
            summary_table.add_row("Files Failed", f"[red]{failed_count}[/red]")
        if corrupted_count > 0:
            summary_table.add_row("Files Corrupted", f"[yellow]{corrupted_count}[/yellow]")
        summary_table.add_row("Total Size", f"{total_bytes/1024/1024:.2f} MB")
        summary_table.add_row("Backup Location", local_path)
        summary_table.add_row("Timestamp", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        console.print(summary_table)
        console.print("="*70 + "\n")
        
        if failed_count == 0 and corrupted_count == 0:
            console.print("[bold green]✅ Backup completed successfully![/bold green]\n")
        else:
            console.print(f"[bold yellow]⚠️  Backup completed with issues. Check logs for details.[/bold yellow]\n")
    
    def _download_files(self, ftp, files_to_download, remote_path, local_path, total_bytes, options):
        """Download files avec retry et vérification d'intégrité"""
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            overall_task = progress.add_task("[blue]Downloading...", total=total_bytes)
            
            success_count = 0
            failed_count = 0
            corrupted_count = 0
            
            for rel_path, size in files_to_download:
                progress.update(overall_task, description=f"[blue]Downloading {rel_path}...")
                
                local_file_path = os.path.join(local_path, rel_path)
                remote_file_path = os.path.join(remote_path, rel_path).replace('\\', '/')
                
                os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                
                # Retry logic (3 tentatives)
                download_success = False
                for attempt in range(3):
                    try:
                        # Vérifier connexion
                        if attempt > 0:
                            try:
                                ftp.voidcmd('NOOP')
                            except:
                                progress.console.log("[yellow]Connection lost. Reconnecting...[/yellow]")
                                ftp = self.connect()
                        
                        # Download
                        with open(local_file_path, 'wb') as f:
                            ftp.retrbinary(
                                f"RETR {remote_file_path}", 
                                lambda data: (f.write(data), progress.update(overall_task, advance=len(data)))[0]
                            )
                        
                        # Vérification d'intégrité
                        if options.get('verify_integrity'):
                            is_valid, msg = self.verify_file_integrity(local_file_path, size)
                            if not is_valid:
                                progress.console.log(f"[yellow]⚠️  Integrity check failed for {rel_path}: {msg}[/yellow]")
                                
                                # Si c'est la dernière tentative
                                if attempt == 2:
                                    os.remove(local_file_path)
                                    corrupted_count += 1
                                    progress.console.log(f"[red]❌ File corrupted after 3 attempts: {rel_path}[/red]")
                                    break
                                else:
                                    # Réessayer
                                    progress.console.log(f"[yellow]Retrying download (attempt {attempt+2}/3)...[/yellow]")
                                    os.remove(local_file_path)
                                    continue
                        
                        # Succès
                        success_count += 1
                        download_success = True
                        break
                        
                    except Exception as e:
                        progress.console.log(f"[yellow]Attempt {attempt+1}/3 failed for {rel_path}: {e}[/yellow]")
                        
                        if attempt == 2:
                            # Dernière tentative échouée
                            progress.console.log(f"[red]❌ Failed permanently: {rel_path}[/red]")
                            failed_count += 1
                            
                            # Supprimer fichier partiel si existe
                            if os.path.exists(local_file_path):
                                os.remove(local_file_path)
            
            return success_count, failed_count, corrupted_count
