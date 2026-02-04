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
        parser = argparse.ArgumentParser(description='Synergy FTP Tool v3.0 - CLI Mode')
        parser.add_argument('action', choices=[
            'backup', 'backup-optimized', 'deploy', 
            'enable-deploy', 'disable-deploy', 'migrate'
        ])
        parser.add_argument('--local', help='Local directory path')
        parser.add_argument('--remote', help='Remote project name')
        parser.add_argument('--dry-run', action='store_true', help='Dry-run mode for deploy')
        parser.add_argument('--no-verify', action='store_true', help='Skip integrity verification')
        parser.add_argument('--no-exclude', action='store_true', help='Do not exclude cache/logs')
        parser.add_argument('--no-incremental', action='store_true', help='Disable incremental scan')
        parser.add_argument('--workers', type=int, default=10, help='Number of parallel workers (default: 10)')
        parser.add_argument('--checkpoint', type=int, default=1000, help='Checkpoint interval (default: 1000)')
        args = parser.parse_args()
        
        tool = SynergyTool()
        
        if args.action == 'enable-deploy':
            # En mode CLI, activation automatique (pour scripts)
            with open(tool.DEPLOY_STATE_FILE, 'w') as f:
                json.dump({
                    'enabled_at': datetime.now().isoformat(),
                    'enabled_by_cli': True
                }, f, indent=4)
            console.print("[green]Deploy mode enabled via CLI[/green]")
            
        elif args.action == 'disable-deploy':
            tool.disable_deploy()
            
        elif args.action == 'backup':
            if not args.local or not args.remote:
                console.print("[red]Error: --local and --remote are required for backup[/red]")
                sys.exit(1)
            
            options = {
                'exclude_patterns': not args.no_exclude,
                'verify_integrity': not args.no_verify,
                'handle_deletions': False,  # En CLI, pas d'interaction
                'parallel_downloads': 0,
            }
            tool.backup(args.local, args.remote, options)
        
        elif args.action == 'backup-optimized':
            if not args.local or not args.remote:
                console.print("[red]Error: --local and --remote are required for backup-optimized[/red]")
                sys.exit(1)
            
            options = {
                'exclude_patterns': not args.no_exclude,
                'verify_integrity': not args.no_verify,
                'handle_deletions': False,  # En CLI, pas d'interaction
                'num_workers': args.workers,
                'use_incremental_scan': not args.no_incremental,
                'checkpoint_interval': args.checkpoint,
            }
            tool.backup_optimized(args.local, args.remote, options)
            
        elif args.action == 'deploy':
            if not args.local or not args.remote:
                console.print("[red]Error: --local and --remote are required for deploy[/red]")
                sys.exit(1)
            tool.deploy(args.local, args.remote, dry_run=args.dry_run)
        
        elif args.action == 'migrate':
            os.system('python migrate_state.py')
    else:
        interactive_menu()