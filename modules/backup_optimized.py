"""
Optimized Backup Module
Utilise tous les composants optimisés pour gérer 1M+ fichiers
"""

import os
import uuid
from datetime import datetime
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich import box

from modules.core import console
from modules.state_manager import StateManager
from modules.parallel_downloader import ParallelDownloader, DownloadTask, DownloadOrganizer
from modules.incremental_scanner import IncrementalScanner

def migrate_json_to_sqlite(json_file: str, db_file: str):
    """Imported/Refactored from migrate_state.py for auto-migration"""
    import json
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        if not data:
            return
        
        state_manager = StateManager(db_file)
        # Migrer par batch
        batch_size = 1000
        items = list(data.items())
        
        for i in range(0, len(items), batch_size):
            batch = dict(items[i:i+batch_size])
            state_manager.update_file_batch(batch, batch_size=batch_size)
        
        # Renommer l'ancien fichier JSON
        backup_name = json_file + ".migrated_backup"
        os.rename(json_file, backup_name)
        console.print(f"[green]✅ Success: Old JSON state migrated to SQLite database.[/green]")
    except Exception as e:
        console.print(f"[red]❌ Migration failed: {e}[/red]")


class BackupOptimizedMixin:
    """
    Backup optimisé pour gérer des millions de fichiers
    Utilise SQLite, téléchargement parallèle, et scan incrémental
    """
    
    def backup_optimized(self, local_path, remote_project_name, options=None):
        """
        Backup optimisé avec tous les composants de haute performance
        
        Args:
            local_path: Chemin local pour sauvegarder
            remote_project_name: Nom du projet sur le serveur
            options: Dict avec configuration
        """
        if options is None:
            options = {
                'exclude_patterns': True,
                'verify_integrity': True,
                'handle_deletions': True,
                'num_workers': 10,  # Nombre de connexions parallèles
                'use_incremental_scan': True,
                'checkpoint_interval': 1000,  # Checkpoint tous les N fichiers
            }

        # Cap workers for SFTP — SSH connections are heavier than FTP
        if self.ftp_port == 22 and options['num_workers'] > 5:
            console.print(f"[yellow]ℹ️  SFTP detected: reducing workers from {options['num_workers']} to 5 "
                         f"(SSH connections are heavier than FTP)[/yellow]")
            options['num_workers'] = 5
        
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        
        # Gérer la migration automatique
        state_json_path = f"state_backup_{remote_project_name.replace('/', '_')}.json"
        state_db_path = f"state_backup_{remote_project_name.replace('/', '_')}.db"
        
        if os.path.exists(state_json_path) and not os.path.exists(state_db_path):
            console.print(f"\n[bold yellow]ℹ️  An old JSON state file was found for '{remote_project_name}'.[/bold yellow]")
            confirm = console.input("[bold]Do you want to migrate it to the new Optimized database? (y/n): [/bold] ")
            if confirm.lower() == 'y':
                migrate_json_to_sqlite(state_json_path, state_db_path)
        
        # Utiliser SQLite au lieu de JSON
        state_manager = StateManager(state_db_path)
        
        # ID unique pour cette synchronisation
        sync_id = str(uuid.uuid4())
        
        if not os.path.exists(local_path):
            os.makedirs(local_path)
        
        console.print("\n" + "="*70)
        console.print(Panel.fit(
            "[bold cyan]🚀 OPTIMIZED BACKUP MODE[/bold cyan]\n\n"
            f"[yellow]Local:[/yellow] {local_path}\n"
            f"[yellow]Remote:[/yellow] {remote_path}\n"
            f"[yellow]Workers:[/yellow] {options['num_workers']} parallel connections\n"
            f"[yellow]Sync ID:[/yellow] {sync_id[:8]}...",
            border_style="cyan",
            box=box.ROUNDED
        ))
        console.print("="*70 + "\n")
        
        # PHASE 1: SCAN REMOTE avec scan incrémental
        console.print("[bold cyan]📡 PHASE 1: Scanning remote server...[/bold cyan]")
        
        with self.connect() as ftp:
            if options.get('use_incremental_scan'):
                # Scan incrémental (beaucoup plus rapide)
                cache_file = f".scan_cache_{remote_project_name.replace('/', '_')}.pkl"

                # Reconnect factory for scanner — creates a fresh connection
                def scanner_reconnect():
                    return self.connect()

                scanner = IncrementalScanner(
                    ftp,
                    remote_path,
                    cache_file=cache_file,
                    incremental_threshold_hours=24,
                    reconnect_factory=scanner_reconnect
                )
                
                def scan_status_callback(stats):
                    if stats.get('strategy') == 'ssh_find':
                        console.print(f"[dim]SSH find: found {stats['files_found']:,} files so far...[/dim]")
                    else:
                        console.print(f"[dim]Scanned {stats['dirs_scanned']} dirs, "
                                    f"found {stats['files_found']} files "
                                    f"(cache hits: {stats['cache_hits']})[/dim]")
                
                remote_files = scanner.scan_smart(status_callback=scan_status_callback)
                scan_stats = scanner.get_statistics()
                
                console.print(f"[green]✅ Scan completed using [bold]{scan_stats['strategy']}[/bold] strategy[/green]")
                console.print(f"[dim]   Dirs scanned: {scan_stats['dirs_scanned']}, "
                            f"Files found: {scan_stats['files_found']}[/dim]")
                if scan_stats.get('reconnections', 0) > 0:
                    console.print(f"[yellow]   Reconnections during scan: {scan_stats['reconnections']}[/yellow]")
                if scan_stats.get('scan_errors', 0) > 0:
                    console.print(f"[yellow]   Directories skipped (errors): {scan_stats['scan_errors']}[/yellow]")
                console.print()
            else:
                # Scan complet classique
                with console.status("[bold cyan]Scanning (full mode)...") as status:
                    remote_files = self.get_remote_files(ftp, remote_path, status=status)
                console.print(f"[green]✅ Full scan completed[/green]\n")
        
        if not remote_files:
            console.print(f"[bold red]❌ No files found in: {remote_path}[/bold red]\n")
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
                console.print(f"[yellow]📦 Excluded {excluded_count:,} files (logs, cache, tmp)[/yellow]\n")
        
        console.print(f"[bold green]✅ Found {len(remote_files):,} files to process[/bold green]\n")
        
        # PHASE 2 & 3: COMPARISON + DELETIONS (memory-efficient via SQLite)
        console.print("[bold cyan]🔍 PHASE 2: Comparing with local state...[/bold cyan]")

        with console.status("[bold cyan]Comparing against database..."):
            files_to_download, total_bytes, deleted_files = state_manager.find_files_to_download(remote_files)

        db_stats = state_manager.get_statistics()
        console.print(f"[dim]   State database: {db_stats['total_files']:,} files cached[/dim]")
        console.print(f"[green]✅ Comparison completed: {len(files_to_download):,} to download, "
                      f"{len(deleted_files):,} deleted on remote[/green]\n")

        # PHASE 3: HANDLE DELETIONS
        if options.get('handle_deletions') and deleted_files:
            console.print(f"[yellow]⚠️  {len(deleted_files):,} files were deleted on remote server[/yellow]")
            self.handle_deleted_files(local_path, deleted_files)

            # Supprimer du state
            state_manager.delete_files(list(deleted_files))
            console.print()
        
        # PHASE 4: DOWNLOAD PARALLÈLE
        if not files_to_download:
            console.print("[bold green]✅ Local backup is up-to-date. Nothing to download.[/bold green]\n")

            # Sauvegarder le state quand meme
            console.print("[bold cyan]💾 Updating state database...[/bold cyan]")
            with console.status("[bold cyan]Saving state..."):
                state_manager.update_file_batch(remote_files, batch_size=5000)
            console.print(f"[green]✅ State database updated[/green]\n")

            # Statistiques finales
            self._show_backup_summary(state_manager, sync_id, 0, 0, 0, datetime.now())
            return
        
        console.print(f"[bold cyan]⬇️  PHASE 4: Downloading {len(files_to_download):,} files "
                     f"({total_bytes/1024/1024:.2f} MB)[/bold cyan]\n")

        start_time = datetime.now()
        success_count = 0
        failed_count = 0
        tar_download_done = False

        # ── TAR STREAMING (bulk SFTP optimization) ──
        TAR_STREAM_THRESHOLD = 500
        if self.ftp_port == 22 and len(files_to_download) >= TAR_STREAM_THRESHOLD:
            try:
                from modules.tar_downloader import TarStreamDownloader

                console.print("[dim]   Attempting tar stream (bulk SFTP optimization)...[/dim]")
                ftp_conn = self.connect()
                tar_dl = TarStreamDownloader(ftp_conn.ssh, remote_path, local_path,
                                             sftp_client=ftp_conn.sftp)

                if tar_dl.is_available():
                    # Decide: full tar (no stdin) vs selective tar (pipe file list)
                    # For full/near-full backups, download_all() is far more reliable
                    # because it avoids piping huge file lists through stdin.
                    use_full_tar = len(files_to_download) >= len(remote_files) * 0.8

                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        TextColumn("•"),
                        TextColumn("{task.completed}/{task.total} files"),
                        TextColumn("•"),
                        TextColumn("[cyan]{task.fields[speed]:.2f} MB/s[/cyan]"),
                        TextColumn("•"),
                        TimeRemainingColumn(),
                        console=console
                    ) as progress:

                        dl_task = progress.add_task(
                            "[cyan]Tar streaming...",
                            total=len(files_to_download),
                            speed=0.0
                        )

                        def tar_progress(completed, total, stats):
                            progress.update(dl_task, completed=completed, speed=stats.get('mbps', 0))

                        if use_full_tar:
                            console.print(f"[dim]   Full directory tar "
                                        f"({len(files_to_download):,}/{len(remote_files):,} files)...[/dim]")
                            tar_stats = tar_dl.download_all(
                                tar_progress, use_compression=True,
                                expected_total=len(files_to_download)
                            )
                        else:
                            rel_paths = [rp for rp, sz in files_to_download]
                            console.print(f"[dim]   Selective tar ({len(rel_paths):,} files)...[/dim]")
                            tar_stats = tar_dl.download_files(
                                rel_paths, tar_progress, use_compression=True
                            )

                    success_count = tar_stats['files_extracted']
                    failed_files = list(tar_stats['errors'])

                    # Verify integrity
                    if options.get('verify_integrity'):
                        console.print("[dim]   Verifying extracted files...[/dim]")
                        expected = {rp: sz for rp, sz in files_to_download}
                        verify_failed = tar_dl.verify_extraction(expected)
                        if verify_failed:
                            failed_files = verify_failed
                            success_count = len(files_to_download) - len(verify_failed)
                            console.print(f"[yellow]   {len(verify_failed)} files need retry via SFTP...[/yellow]")

                    ftp_conn.close()

                    # Retry failed files with per-file SFTP
                    if failed_files:
                        size_lookup = dict(files_to_download)
                        retry_tasks = [
                            DownloadTask(
                                rel_path=rp,
                                remote_path=os.path.join(remote_path, rp).replace('\\', '/'),
                                local_path=os.path.join(local_path, rp),
                                size=size_lookup.get(rp, 0),
                                priority=0
                            )
                            for rp in failed_files
                        ]

                        console.print(f"[dim]   Retrying {len(retry_tasks)} files via per-file SFTP...[/dim]")
                        retry_dl = ParallelDownloader(
                            ftp_host=self.ftp_host,
                            ftp_port=self.ftp_port,
                            ftp_user=self.ftp_user,
                            ftp_pass=self.ftp_pass,
                            num_workers=min(3, options['num_workers']),
                            max_retries=3,
                            verify_integrity=options.get('verify_integrity', True)
                        )
                        retry_dl.add_tasks(retry_tasks)
                        retry_dl.start()
                        retry_dl.wait_completion()
                        retry_dl.stop()

                        for result in retry_dl.collect_results():
                            if result.success:
                                success_count += 1
                            else:
                                state_manager.log_error(
                                    sync_id=sync_id,
                                    rel_path=result.rel_path,
                                    error_message=result.error_message or "Unknown error",
                                    retry_count=result.retry_count
                                )

                    failed_count = len(files_to_download) - success_count
                    tar_download_done = True

                    console.print(f"\n[green]✅ Download phase completed (tar stream)[/green]")
                    console.print(f"[dim]   Success: {success_count:,} | Failed: {failed_count:,}[/dim]\n")
                else:
                    ftp_conn.close()
                    console.print("[dim]   tar not available on server, using per-file download...[/dim]")
            except Exception as e:
                console.print(f"[yellow]   Tar stream error: {e}[/yellow]")
                console.print("[dim]   Falling back to per-file download...[/dim]")

        # ── PER-FILE PARALLEL DOWNLOAD (fallback / default for FTP) ──
        if not tar_download_done:
            download_tasks = []
            for rel_path, size in files_to_download:
                remote_file_path = os.path.join(remote_path, rel_path).replace('\\', '/')
                local_file_path = os.path.join(local_path, rel_path)

                task = DownloadTask(
                    rel_path=rel_path,
                    remote_path=remote_file_path,
                    local_path=local_file_path,
                    size=size,
                    priority=0
                )
                download_tasks.append(task)

            console.print("[dim]   Organizing downloads with hybrid strategy...[/dim]")
            download_tasks = DownloadOrganizer.prioritize_hybrid(download_tasks)

            downloader = ParallelDownloader(
                ftp_host=self.ftp_host,
                ftp_port=self.ftp_port,
                ftp_user=self.ftp_user,
                ftp_pass=self.ftp_pass,
                num_workers=options['num_workers'],
                max_retries=3,
                verify_integrity=options.get('verify_integrity', True)
            )

            downloader.add_tasks(download_tasks)
            downloader.start()

            last_checkpoint = 0
            checkpoint_interval = options.get('checkpoint_interval', 1000)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TextColumn("{task.completed}/{task.total} files"),
                TextColumn("•"),
                TextColumn("[cyan]{task.fields[speed]:.2f} MB/s[/cyan]"),
                TextColumn("•"),
                TimeRemainingColumn(),
                console=console
            ) as progress:

                download_task = progress.add_task(
                    "[cyan]Downloading...",
                    total=len(files_to_download),
                    speed=0.0
                )

                def progress_callback(completed, total, stats):
                    nonlocal last_checkpoint

                    speed_mbps = stats.get('mbps', 0)
                    progress.update(
                        download_task,
                        completed=completed,
                        speed=speed_mbps
                    )

                    if completed - last_checkpoint >= checkpoint_interval:
                        state_manager.create_checkpoint(
                            sync_id=sync_id,
                            files_processed=completed,
                            files_total=total,
                            bytes_transferred=stats.get('bytes_transferred', 0),
                            status='in_progress'
                        )
                        last_checkpoint = completed

                downloader.wait_completion(progress_callback)

            downloader.stop()

            results = downloader.collect_results()

            success_count = sum(1 for r in results if r.success)
            failed_count = sum(1 for r in results if not r.success)

            for result in results:
                if not result.success:
                    state_manager.log_error(
                        sync_id=sync_id,
                        rel_path=result.rel_path,
                        error_message=result.error_message or "Unknown error",
                        retry_count=result.retry_count
                    )

            console.print(f"\n[green]✅ Download phase completed[/green]")
            console.print(f"[dim]   Success: {success_count:,} | Failed: {failed_count:,}[/dim]\n")
        
        # PHASE 5: UPDATE STATE DATABASE
        console.print("[bold cyan]💾 PHASE 5: Updating state database...[/bold cyan]")
        
        with console.status("[bold cyan]Saving state..."):
            state_manager.update_file_batch(remote_files, batch_size=5000)
        
        console.print(f"[green]✅ State database updated[/green]\n")
        
        # Checkpoint final
        state_manager.create_checkpoint(
            sync_id=sync_id,
            files_processed=len(files_to_download),
            files_total=len(files_to_download),
            bytes_transferred=total_bytes,
            status='completed' if failed_count == 0 else 'completed_with_errors'
        )
        
        # SUMMARY
        self._show_backup_summary(
            state_manager, sync_id, 
            success_count, failed_count, 
            total_bytes, start_time
        )
    
    def _show_backup_summary(self, state_manager: StateManager, sync_id: str, 
                            success_count: int, failed_count: int, 
                            bytes_transferred: int, start_time: datetime):
        """Affiche le résumé final du backup"""
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        console.print("\n" + "="*70)
        
        # Tableau de résumé
        summary_table = Table(
            title="📊 BACKUP SUMMARY", 
            show_header=True, 
            header_style="bold magenta",
            box=box.ROUNDED
        )
        summary_table.add_column("Metric", style="cyan", width=30)
        summary_table.add_column("Value", style="green", justify="right")
        
        # Stats de la base
        db_stats = state_manager.get_statistics()
        
        summary_table.add_row("Files in Database", f"{db_stats['total_files']:,}")
        summary_table.add_row("Total Size", f"{db_stats['total_size_mb']:.2f} MB")
        
        if success_count > 0:
            summary_table.add_row("Files Downloaded", f"[green]{success_count:,}[/green]")
        
        if failed_count > 0:
            summary_table.add_row("Files Failed", f"[red]{failed_count:,}[/red]")
        
        if bytes_transferred > 0:
            summary_table.add_row("Data Transferred", f"{bytes_transferred/1024/1024:.2f} MB")
        
        if duration > 0:
            summary_table.add_row("Duration", f"{duration:.1f} seconds")
            
            if bytes_transferred > 0:
                speed_mbps = (bytes_transferred / duration) / (1024 * 1024)
                summary_table.add_row("Average Speed", f"{speed_mbps:.2f} MB/s")
        
        summary_table.add_row("Database Size", f"{db_stats['database_size_mb']:.2f} MB")
        summary_table.add_row("Last Sync", db_stats.get('last_sync', 'N/A'))
        summary_table.add_row("Sync ID", sync_id[:8] + "...")
        
        console.print(summary_table)
        console.print("="*70 + "\n")
        
        # Erreurs si présentes
        if failed_count > 0:
            errors = state_manager.get_errors(sync_id)
            if errors:
                console.print("[bold yellow]⚠️  Errors encountered:[/bold yellow]\n")
                
                error_table = Table(show_header=True, box=box.SIMPLE)
                error_table.add_column("File", style="red", width=50)
                error_table.add_column("Error", style="yellow", width=40)
                error_table.add_column("Retries", justify="center")
                
                for error in errors[:10]:  # Montrer les 10 premières
                    error_table.add_row(
                        error['rel_path'],
                        error['error_message'][:40] + "..." if len(error['error_message']) > 40 else error['error_message'],
                        str(error['retry_count'])
                    )
                
                if len(errors) > 10:
                    error_table.add_row(f"... and {len(errors)-10} more errors", "", "")
                
                console.print(error_table)
                console.print()
        
        # Message final
        if failed_count == 0:
            console.print(Panel.fit(
                "[bold green]✅ BACKUP COMPLETED SUCCESSFULLY![/bold green]\n\n"
                "[cyan]All files have been synchronized.[/cyan]",
                border_style="green",
                box=box.DOUBLE
            ))
        else:
            console.print(Panel.fit(
                f"[bold yellow]⚠️  BACKUP COMPLETED WITH {failed_count} ERRORS[/bold yellow]\n\n"
                "[yellow]Check the error log above for details.[/yellow]\n"
                "[cyan]You can retry the backup to download failed files.[/cyan]",
                border_style="yellow",
                box=box.DOUBLE
            ))
        
        console.print()