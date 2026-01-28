import os
import json
import logging
import sys
from datetime import datetime

# Import modular components
from modules.core import SynergyCore, console
from modules.deploy import DeployMixin
from modules.backup import BackupMixin

class SynergyTool(SynergyCore, DeployMixin, BackupMixin):
    """
    Combined tool using mixins for deployment and backup logic.
    Inherits base FTP and utility functionality from SynergyCore.
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
        console.print("[bold magenta]║     🚀 SYNERGY FTP TOOL v2.0 🚀      ║[/bold magenta]")
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
        
        console.print("3. [bold cyan]📥 Backup[/bold cyan] (FTP → Local)")
        console.print("4. [bold yellow]⚙️  Setup .env file[/bold yellow] (Initial configuration)")
        console.print("5. [bold red]❌ Exit[/bold red]")
        
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
        
        # Option 3 : Backup
        if choice == '3':
            console.print("\n[bold cyan]═══ BACKUP MODE ═══[/bold cyan]\n")
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
        
        # Option 4 : Setup .env
        if choice == '4':
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
        
        # Option 5 : Exit
        if choice == '5':
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
        parser = argparse.ArgumentParser(description='Synergy FTP Tool - CLI Mode')
        parser.add_argument('action', choices=['backup', 'deploy', 'enable-deploy', 'disable-deploy'])
        parser.add_argument('--local', help='Local directory path')
        parser.add_argument('--remote', help='Remote project name')
        parser.add_argument('--dry-run', action='store_true', help='Dry-run mode for deploy')
        parser.add_argument('--no-verify', action='store_true', help='Skip integrity verification (backup)')
        parser.add_argument('--no-exclude', action='store_true', help='Do not exclude cache/logs (backup)')
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
            
        elif args.action == 'deploy':
            if not args.local or not args.remote:
                console.print("[red]Error: --local and --remote are required for deploy[/red]")
                sys.exit(1)
            tool.deploy(args.local, args.remote, dry_run=args.dry_run)
    else:
        interactive_menu()