import os
import json
import logging
import sys
from datetime import datetime

# Import modular components
from modules.core import SynergyCore, console
from modules.deploy import DeployMixin
from modules.backup import BackupMixin
from modules.backup_optimized import BackupOptimizedMixin

class SynergyTool(SynergyCore, DeployMixin, BackupMixin, BackupOptimizedMixin):
    """
    Combined tool using mixins for deployment and backup logic.
    Inherits base FTP and utility functionality from SynergyCore.
    Includes optimized backup for handling 1M+ files.
    """
    pass

# ============================================
# MENU INTERACTIF
# ============================================

def interactive_menu():
    tool = SynergyTool()
    
    while True:
        console.clear()
        console.print("\n[bold magenta]╔═══════════════════════════════════════╗[/bold magenta]")
        console.print("[bold magenta]║     🚀 SYNERGY FTP TOOL v3.0 🚀      ║[/bold magenta]")
        console.print("[bold magenta]║         Optimized Edition             ║[/bold magenta]")
        console.print("[bold magenta]╚═══════════════════════════════════════╝[/bold magenta]\n")
        
        # Afficher le statut du deploy
        deploy_status = tool.is_deploy_enabled()
        if deploy_status:
            console.print("[bold red]⚠️  DEPLOY MODE: ENABLED (Use with caution!)[/bold red]\n")
        else:
            console.print("[bold green]🛡️  DEPLOY MODE: DISABLED (FTP is protected)[/bold green]\n")
        
        console.print("[bold cyan]Available Actions:[/bold cyan]\n")
        
        if deploy_status:
            console.print("1. [bold yellow]🔴 Disable Deploy Mode[/bold yellow] (Recommended)")
            console.print("2. [bold cyan]🚀 Deploy[/bold cyan] (Local → FTP) [bold red]⚠️[/bold red]")
        else:
            console.print("1. [bold green]🔓 Enable Deploy Mode[/bold green] (Requires confirmation)")
        
        console.print("3. [bold cyan]📥 Backup[/bold cyan] (FTP → Local) [dim]- Classic mode[/dim]")
        console.print("4. [bold green]⚡ Backup Optimized[/bold green] (FTP → Local) [bold]- NEW! For 1M+ files[/bold]")
        console.print("5. [bold yellow]⚙️  Setup .env file[/bold yellow] (Initial configuration)")
        console.print("6. [bold red]❌ Exit[/bold red]")
        
        choice = console.input("\n[bold]Your choice:[/bold] ")
        
        # Option 1 : Toggle deploy mode
        if choice == '1':
            if deploy_status:
                tool.disable_deploy()
            else:
                tool.enable_deploy()
            console.input("\n[dim]Press Enter to continue...[/dim]")
            continue
        
        # Option 2 : Deploy (seulement si activé)
        if choice == '2' and deploy_status:
            console.print("\n[bold yellow]═══ DEPLOY MODE ═══[/bold yellow]\n")
            local_dir = console.input("[bold]Local directory (ex: ./project):[/bold] ") or "./project"
            project_name = console.input(f"[bold]Project name on server (default: {os.path.basename(local_dir)}):[/bold] ") or os.path.basename(local_dir)
            
            # Demander si dry-run
            console.print("\n[bold yellow]⚠️  It's HIGHLY RECOMMENDED to run a dry-run first![/bold yellow]")
            dry_run_choice = console.input("[bold]Run in DRY-RUN mode first? (y/n):[/bold] ")
            
            if dry_run_choice.lower() in ['y', 'yes']:
                tool.deploy(local_dir, project_name, dry_run=True)
                
                # Proposer de faire le vrai deploy après
                console.print()
                real_deploy = console.input("[bold]Execute REAL deploy now? (yes/no):[/bold] ")
                if real_deploy.lower() in ['yes']:
                    tool.deploy(local_dir, project_name, dry_run=False)
            else:
                tool.deploy(local_dir, project_name, dry_run=False)
            
            console.input("\n[dim]Press Enter to continue...[/dim]")
            continue
        
        # Option 3 : Backup classique
        if choice == '3':
            console.print("\n[bold cyan]═══ BACKUP MODE (CLASSIC) ═══[/bold cyan]\n")
            console.print("[dim]ℹ️  Use this for < 10,000 files. For larger projects, use Optimized Backup.[/dim]\n")
            
            local_dir = console.input("[bold]Local backup directory (ex: ./backup):[/bold] ") or "./backup"
            project_name = console.input(f"[bold]Remote project name (default: {os.path.basename(local_dir)}):[/bold] ") or os.path.basename(local_dir)
            
            # Options avancées
            console.print("\n[bold yellow]💡 Advanced Options (recommended defaults):[/bold yellow]")
            exclude = console.input("Exclude cache/logs/tmp files? (y/n) [y]: ") or "y"
            verify = console.input("Verify file integrity after download? (y/n) [y]: ") or "y"
            handle_del = console.input("Handle deleted files? (y/n) [y]: ") or "y"
            
            options = {
                'exclude_patterns': exclude.lower() == 'y',
                'verify_integrity': verify.lower() == 'y',
                'handle_deletions': handle_del.lower() == 'y',
                'parallel_downloads': 0,  # Séquentiel (plus stable)
            }
            
            tool.backup(local_dir, project_name, options)
            console.input("\n[dim]Press Enter to continue...[/dim]")
            continue
        
        # Option 4 : Backup optimisé (NOUVEAU)
        if choice == '4':
            console.print("\n[bold green]═══ BACKUP MODE (OPTIMIZED) ⚡ ═══[/bold green]\n")
            console.print("[bold cyan]✨ Features:[/bold cyan]")
            console.print("  • SQLite database (75% less memory)")
            console.print("  • Parallel downloads (98% faster)")
            console.print("  • Incremental scanning (95% faster)")
            console.print("  • Auto-resume after crash")
            console.print("  • Real-time statistics\n")
            
            local_dir = console.input("[bold]Local backup directory (ex: ./backup):[/bold] ") or "./backup"
            project_name = console.input(f"[bold]Remote project name (default: {os.path.basename(local_dir)}):[/bold] ") or os.path.basename(local_dir)
            
            # Options avancées
            console.print("\n[bold yellow]💡 Configuration:[/bold yellow]")
            
            # Déterminer le nombre de workers selon la connexion
            is_sftp = tool.ftp_port == 22
            if is_sftp:
                console.print("\n[dim]SFTP detected — workers are capped at 5 (SSH connections are heavier)[/dim]")
                console.print("[dim]Connection speed:[/dim]")
                console.print("  1. Slow connection → 2 workers")
                console.print("  2. Normal connection → 3 workers [recommended]")
                console.print("  3. Fast connection → 5 workers")
                speed_choice = console.input("[bold]Your choice (1-3) [2]:[/bold] ") or "2"
                workers_map = {"1": 2, "2": 3, "3": 5}
                workers = workers_map.get(speed_choice, 3)
            else:
                console.print("\n[dim]Connection speed:[/dim]")
                console.print("  1. ADSL (< 10 Mbps) → 3-5 workers")
                console.print("  2. Home Fiber (100 Mbps) → 10-15 workers [recommended]")
                console.print("  3. Pro Fiber (1 Gbps) → 15-25 workers")
                console.print("  4. Datacenter → 20-50 workers")
                speed_choice = console.input("[bold]Your choice (1-4) [2]:[/bold] ") or "2"
                workers_map = {"1": 5, "2": 10, "3": 20, "4": 30}
                workers = workers_map.get(speed_choice, 10)
            
            exclude = console.input("\nExclude cache/logs/tmp files? (y/n) [y]: ") or "y"
            verify = console.input("Verify file integrity? (y/n) [y]: ") or "y"
            handle_del = console.input("Handle deleted files? (y/n) [y]: ") or "y"
            incremental = console.input("Use incremental scan? (y/n) [y]: ") or "y"
            
            options = {
                'exclude_patterns': exclude.lower() == 'y',
                'verify_integrity': verify.lower() == 'y',
                'handle_deletions': handle_del.lower() == 'y',
                'num_workers': workers,
                'use_incremental_scan': incremental.lower() == 'y',
                'checkpoint_interval': 1000,
            }
            
            console.print(f"\n[bold green]Starting optimized backup with {workers} parallel workers...[/bold green]\n")
            
            tool.backup_optimized(local_dir, project_name, options)
            console.input("\n[dim]Press Enter to continue...[/dim]")
            continue
        
        # Option 5 : Setup .env
        if choice == '5':
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
            console.input("\n[dim]Press Enter to continue...[/dim]")
            continue
        
        # Option 6 : Exit
        if choice == '6':
            console.print("\n[bold green]👋 Goodbye![/bold green]\n")
            break
        
        # Choix invalide
        console.print("\n[bold red]❌ Invalid choice. Please try again.[/bold red]")
        console.input("\n[dim]Press Enter to continue...[/dim]")

# ============================================
# MODE CLI (pour automatisation)
# ============================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser(
            description='Synergy FTP Tool v3.0 - CLI Mode',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Quick backup (direct launch, no menu):
  python nas_tool.py -target "../backup" -distant_folder "." -speed 3

  # Full options:
  python nas_tool.py -target "../backup" -distant_folder "/www/PACKAGE" -ignore_log_cache_temp 0 -verify_integrity 1 -speed 3 -auto_increment 1 -handle_deleted 1

  # Classic argparse style (still supported):
  python nas_tool.py backup-optimized --local ../backup --remote .
"""
        )

        # Support both styles: new simple flags and old positional action
        parser.add_argument('action', nargs='?', default=None, choices=[
            'backup', 'backup-optimized', 'deploy',
            'enable-deploy', 'disable-deploy', 'migrate'
        ], help='Action to perform (optional if using -target/-distant_folder)')

        # New simple flags
        parser.add_argument('-target', help='Local backup directory path')
        parser.add_argument('-distant_folder', help='Remote project/folder name on server')
        parser.add_argument('-ignore_log_cache_temp', type=int, default=1, choices=[0, 1],
                            help='Exclude cache/logs/tmp files (1=yes, 0=no, default: 1)')
        parser.add_argument('-verify_integrity', type=int, default=1, choices=[0, 1],
                            help='Verify file integrity after download (1=yes, 0=no, default: 1)')
        parser.add_argument('-speed', type=int, default=2, choices=[1, 2, 3, 4],
                            help='Speed preset: 1=slow, 2=normal, 3=fast, 4=max (default: 2)')
        parser.add_argument('-auto_increment', type=int, default=1, choices=[0, 1],
                            help='Use incremental scan (1=yes, 0=no, default: 1)')
        parser.add_argument('-handle_deleted', type=int, default=1, choices=[0, 1],
                            help='Handle files deleted on remote (1=yes, 0=no, default: 1)')

        # Old-style flags (still supported)
        parser.add_argument('--local', help='Local directory path (alias for -target)')
        parser.add_argument('--remote', help='Remote project name (alias for -distant_folder)')
        parser.add_argument('--dry-run', action='store_true', help='Dry-run mode for deploy')
        parser.add_argument('--no-verify', action='store_true', help='Skip integrity verification')
        parser.add_argument('--no-exclude', action='store_true', help='Do not exclude cache/logs')
        parser.add_argument('--no-incremental', action='store_true', help='Disable incremental scan')
        parser.add_argument('--workers', type=int, default=None, help='Number of parallel workers (overrides -speed)')
        parser.add_argument('--checkpoint', type=int, default=1000, help='Checkpoint interval (default: 1000)')
        args = parser.parse_args()

        # Resolve local/remote from either flag style
        local_path = args.target or args.local
        remote_name = args.distant_folder or args.remote

        tool = SynergyTool()

        # If -target and -distant_folder are provided without action, default to backup-optimized
        if args.action is None:
            if local_path and remote_name:
                args.action = 'backup-optimized'
            else:
                parser.print_help()
                sys.exit(1)

        if args.action == 'enable-deploy':
            with open(tool.DEPLOY_STATE_FILE, 'w') as f:
                json.dump({
                    'enabled_at': datetime.now().isoformat(),
                    'enabled_by_cli': True
                }, f, indent=4)
            console.print("[green]Deploy mode enabled via CLI[/green]")

        elif args.action == 'disable-deploy':
            tool.disable_deploy()

        elif args.action == 'backup':
            if not local_path or not remote_name:
                console.print("[red]Error: -target and -distant_folder (or --local and --remote) are required[/red]")
                sys.exit(1)

            options = {
                'exclude_patterns': bool(args.ignore_log_cache_temp) and not args.no_exclude,
                'verify_integrity': bool(args.verify_integrity) and not args.no_verify,
                'handle_deletions': bool(args.handle_deleted),
                'parallel_downloads': 0,
            }
            tool.backup(local_path, remote_name, options)

        elif args.action == 'backup-optimized':
            if not local_path or not remote_name:
                console.print("[red]Error: -target and -distant_folder (or --local and --remote) are required[/red]")
                sys.exit(1)

            # Speed preset -> workers mapping
            is_sftp = tool.ftp_port == 22
            if args.workers is not None:
                workers = args.workers
            elif is_sftp:
                workers = {1: 2, 2: 3, 3: 5, 4: 5}.get(args.speed, 3)
            else:
                workers = {1: 5, 2: 10, 3: 20, 4: 30}.get(args.speed, 10)

            options = {
                'exclude_patterns': bool(args.ignore_log_cache_temp) and not args.no_exclude,
                'verify_integrity': bool(args.verify_integrity) and not args.no_verify,
                'handle_deletions': bool(args.handle_deleted),
                'num_workers': workers,
                'use_incremental_scan': bool(args.auto_increment) and not args.no_incremental,
                'checkpoint_interval': args.checkpoint,
            }

            console.print(f"[bold green]CLI: Starting optimized backup with {workers} workers...[/bold green]")
            console.print(f"[dim]  Local: {local_path}[/dim]")
            console.print(f"[dim]  Remote: {remote_name}[/dim]")
            console.print(f"[dim]  Options: exclude={options['exclude_patterns']}, verify={options['verify_integrity']}, "
                         f"incremental={options['use_incremental_scan']}, deleted={options['handle_deletions']}[/dim]\n")

            tool.backup_optimized(local_path, remote_name, options)

        elif args.action == 'deploy':
            if not local_path or not remote_name:
                console.print("[red]Error: -target and -distant_folder (or --local and --remote) are required[/red]")
                sys.exit(1)
            tool.deploy(local_path, remote_name, dry_run=args.dry_run)

        elif args.action == 'migrate':
            os.system('python migrate_state.py')
    else:
        interactive_menu()