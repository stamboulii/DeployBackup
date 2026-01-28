import os
import json
from datetime import datetime
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TransferSpeedColumn, TimeRemainingColumn

from modules.core import console

class DeployMixin:
    def is_deploy_enabled(self):
        """Vérifie si le deploy est activé"""
        return os.path.exists(self.DEPLOY_STATE_FILE)
    
    def enable_deploy(self):
        """Active le mode deploy avec confirmation"""
        console.print("\n" + "="*70)
        console.print(Panel.fit(
            "[bold red]⚠️  DEPLOY MODE ACTIVATION ⚠️[/bold red]\n\n"
            "[yellow]Deploy mode allows uploading files to your FTP server.[/yellow]\n"
            "[yellow]This can OVERWRITE or DELETE files on the remote server.[/yellow]\n\n"
            "[bold]Only enable this if you understand the risks![/bold]",
            border_style="red"
        ))
        console.print("="*70 + "\n")
        
        # Protection 1 : Afficher les chemins protégés
        protected_table = Table(title="Protected Paths (Cannot Deploy Here)")
        protected_table.add_column("Path", style="red bold")
        for path in self.PROTECTED_PATHS:
            protected_table.add_row(path)
        console.print(protected_table)
        console.print()
        
        # Protection 2 : Confirmation par phrase complète
        console.print("[bold yellow]To enable deploy mode, you must type:[/bold yellow]")
        console.print("[bold red]'I UNDERSTAND THE RISKS'[/bold red]\n")
        
        confirmation = console.input("[bold]Type here:[/bold] ")
        
        if confirmation == "I UNDERSTAND THE RISKS":
            # Créer le fichier d'activation avec timestamp
            with open(self.DEPLOY_STATE_FILE, 'w') as f:
                json.dump({
                    'enabled_at': datetime.now().isoformat(),
                    'enabled_by_user': True
                }, f, indent=4)
            
            console.print("\n[bold green]✅ Deploy mode ENABLED[/bold green]")
            console.print("[yellow]⚠️  Remember: Always use dry-run first![/yellow]\n")
        else:
            console.print("\n[bold red]❌ Incorrect confirmation. Deploy mode remains DISABLED.[/bold red]\n")
    
    def disable_deploy(self):
        """Désactive le mode deploy"""
        if os.path.exists(self.DEPLOY_STATE_FILE):
            os.remove(self.DEPLOY_STATE_FILE)
            console.print("\n[bold green]✅ Deploy mode DISABLED[/bold green]")
            console.print("[cyan]Your FTP server is now safe from accidental uploads.[/cyan]\n")
        else:
            console.print("\n[yellow]ℹ️  Deploy mode is already disabled.[/yellow]\n")

    def deploy(self, local_path, remote_project_name, dry_run=False):
        """
        Deploy files from local to FTP with multiple safety checks
        """
        # 🛡️ PROTECTION 0 : Vérifier que deploy est activé
        if not self.is_deploy_enabled():
            console.print("\n" + "="*70)
            console.print(Panel.fit(
                "[bold red]❌ DEPLOY MODE IS DISABLED[/bold red]\n\n"
                "[yellow]For your safety, deploy mode is currently disabled.[/yellow]\n"
                "[yellow]To enable it, select 'Enable Deploy Mode' from the menu.[/yellow]",
                border_style="red"
            ))
            console.print("="*70 + "\n")
            return
        
        remote_path = os.path.join(self.remote_base, remote_project_name).replace('\\', '/')
        
        # 🛡️ PROTECTION 1 : Vérifier chemins protégés
        if self.is_protected_path(remote_path):
            console.print("\n" + "="*70)
            console.print(Panel.fit(
                f"[bold red]🚨 CRITICAL: '{remote_path}' IS A PROTECTED PATH![/bold red]\n\n"
                "[red]Deploying to this location is FORBIDDEN.[/red]\n"
                "[yellow]It could overwrite critical system files.[/yellow]",
                border_style="red"
            ))
            console.print("="*70 + "\n")
            return
        
        # 🛡️ PROTECTION 2 : Afficher configuration détaillée
        console.print("\n" + "="*70)
        config_table = Table(title="🚀 Deploy Configuration", show_header=True, header_style="bold magenta")
        config_table.add_column("Property", style="cyan", width=20)
        config_table.add_column("Value", style="green")
        config_table.add_row("Mode", "[bold yellow]DRY-RUN (Preview Only)[/bold yellow]" if dry_run else "[bold red]REAL DEPLOY (Will Upload)[/bold red]")
        config_table.add_row("Local Path", local_path)
        config_table.add_row("Remote Path", remote_path)
        config_table.add_row("FTP Server", self.ftp_host)
        config_table.add_row("Remote Base", self.remote_base)
        console.print(config_table)
        console.print("="*70 + "\n")
        
        # 🛡️ PROTECTION 3 : Première confirmation (sauf en dry-run)
        if not dry_run:
            console.print("[bold yellow]⚠️  This will upload files to your FTP server![/bold yellow]")
            confirm1 = console.input("[bold]Do you want to continue? (yes/no):[/bold] ")
            if confirm1.lower() not in ['yes', 'y']:
                console.print("\n[green]✅ Deploy cancelled. Good decision![/green]\n")
                return
        
        state_file = f"state_deploy_{remote_project_name.replace('/', '_')}.json"
        current_state = self.load_state(state_file)
        
        with console.status("[bold green]Scanning local files...") as status:
            local_files = self.get_local_files(local_path)
            console.print(f"[green]✅ Found {len(local_files)} local files.[/green]\n")

        if not local_files:
            console.print("[bold red]❌ No files found in local directory![/bold red]\n")
            return

        new_state = {}
        files_to_upload = []
        total_bytes = 0
        
        # Calculer les fichiers à uploader
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
            console.print("[bold green]✅ Everything is up-to-date. No files to deploy.[/bold green]\n")
            self.save_state(state_file, new_state)
            return

        # 🛡️ PROTECTION 4 : Afficher preview détaillé
        console.print("="*70)
        preview_table = Table(
            title=f"📋 Files to Upload ({len(files_to_upload)} files, {total_bytes/1024/1024:.2f} MB)",
            show_header=True,
            header_style="bold cyan"
        )
        preview_table.add_column("File", style="cyan", width=40)
        preview_table.add_column("Size", style="green", justify="right")
        preview_table.add_column("Target", style="yellow", width=50)
        
        # Afficher les 15 premiers fichiers
        for rel_path, target_remote, size in files_to_upload[:15]:
            preview_table.add_row(
                rel_path,
                f"{size/1024:.2f} KB" if size < 1024*1024 else f"{size/1024/1024:.2f} MB",
                target_remote
            )
        
        if len(files_to_upload) > 15:
            preview_table.add_row(
                f"[bold]... and {len(files_to_upload)-15} more files[/bold]",
                "",
                ""
            )
        
        console.print(preview_table)
        console.print("="*70 + "\n")

        # MODE DRY-RUN : Arrêter ici
        if dry_run:
            console.print(Panel.fit(
                "[bold yellow]🔍 DRY-RUN MODE - PREVIEW ONLY[/bold yellow]\n\n"
                f"[cyan]• {len(files_to_upload)} files would be uploaded[/cyan]\n"
                f"[cyan]• Total size: {total_bytes/1024/1024:.2f} MB[/cyan]\n"
                f"[cyan]• Target: {remote_path}[/cyan]\n\n"
                "[green]No files were actually uploaded.[/green]\n"
                "[yellow]Run again without dry-run to execute real deploy.[/yellow]",
                border_style="yellow"
            ))
            console.print()
            return

        # 🛡️ PROTECTION 5 : Confirmation finale pour REAL DEPLOY
        console.print("[bold red]⚠️  FINAL CONFIRMATION ⚠️[/bold red]")
        console.print(f"[yellow]You are about to upload {len(files_to_upload)} files ({total_bytes/1024/1024:.2f} MB)[/yellow]")
        console.print(f"[yellow]To: {remote_path}[/yellow]\n")
        
        final_confirm = console.input("[bold red]Type 'DEPLOY' to proceed:[/bold red] ")
        
        if final_confirm != 'DEPLOY':
            console.print("\n[green]✅ Deploy cancelled by user.[/green]\n")
            return

        # 🚀 DEPLOY RÉEL
        console.print("\n[bold green]🚀 Starting real deployment...[/bold green]\n")
        
        with self.connect() as ftp:
            self.ensure_remote_dir(ftp, remote_path)
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                overall_task = progress.add_task("[green]Uploading...", total=total_bytes)
                
                upload_success = 0
                upload_failed = 0
                
                for rel_path, target_remote, size in files_to_upload:
                    progress.update(overall_task, description=f"[green]Uploading {rel_path}...")
                    
                    self.ensure_remote_dir(ftp, os.path.dirname(target_remote))
                    
                    try:
                        with open(os.path.join(local_path, rel_path), 'rb') as f:
                            ftp.storbinary(
                                f"STOR {target_remote}", 
                                f, 
                                callback=lambda data: progress.update(overall_task, advance=len(data))
                            )
                        upload_success += 1
                    except Exception as e:
                        upload_failed += 1
                        progress.console.log(f"[red]❌ Failed: {rel_path} - {e}[/red]")
        
        self.save_state(state_file, new_state)
        
        # Résumé final
        console.print("\n" + "="*70)
        summary_table = Table(title="📊 Deploy Summary", show_header=True, header_style="bold magenta")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Value", style="green", justify="right")
        summary_table.add_row("Files Uploaded", f"[green]{upload_success}[/green]")
        if upload_failed > 0:
            summary_table.add_row("Files Failed", f"[red]{upload_failed}[/red]")
        summary_table.add_row("Total Size", f"{total_bytes/1024/1024:.2f} MB")
        summary_table.add_row("Target Path", remote_path)
        console.print(summary_table)
        console.print("="*70 + "\n")
        
        if upload_failed == 0:
            console.print("[bold green]✅ Deployment completed successfully![/bold green]\n")
        else:
            console.print(f"[bold yellow]⚠️  Deployment completed with {upload_failed} errors. Check logs.[/bold yellow]\n")
