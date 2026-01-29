"""
Migration Utility - Convert JSON state files to SQLite database
"""

import os
import json
import glob
from modules.state_manager import StateManager
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

console = Console()


def migrate_json_to_sqlite(json_file: str, db_file: str):
    """
    Migre un fichier JSON state vers une base SQLite
    
    Args:
        json_file: Chemin vers le fichier JSON à migrer
        db_file: Chemin vers la base SQLite de destination
    """
    console.print(f"[cyan]Migrating {json_file} → {db_file}[/cyan]")
    
    # Charger le JSON
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        if not data:
            console.print(f"[yellow]⚠️  Empty file, skipping[/yellow]\n")
            return
        
        console.print(f"[dim]   Loaded {len(data):,} entries from JSON[/dim]")
        
        # Créer le state manager
        state_manager = StateManager(db_file)
        
        # Migrer par batch
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Migrating...", total=len(data))
            
            batch_size = 1000
            items = list(data.items())
            
            for i in range(0, len(items), batch_size):
                batch = dict(items[i:i+batch_size])
                state_manager.update_file_batch(batch, batch_size=batch_size)
                progress.update(task, advance=len(batch))
        
        # Statistiques
        stats = state_manager.get_statistics()
        console.print(f"[green]✅ Migration completed[/green]")
        console.print(f"[dim]   Files in database: {stats['total_files']:,}[/dim]")
        console.print(f"[dim]   Database size: {stats['database_size_mb']:.2f} MB[/dim]")
        
        # Renommer l'ancien fichier JSON
        backup_name = json_file + ".migrated_backup"
        os.rename(json_file, backup_name)
        console.print(f"[dim]   Original JSON backed up to: {backup_name}[/dim]\n")
        
    except Exception as e:
        console.print(f"[red]❌ Migration failed: {e}[/red]\n")


def migrate_all_state_files():
    """
    Migre automatiquement tous les fichiers state_*.json trouvés
    """
    console.print("\n[bold cyan]🔄 STATE FILE MIGRATION UTILITY[/bold cyan]\n")
    console.print("Searching for state_*.json files...\n")
    
    # Trouver tous les fichiers state
    json_files = glob.glob("state_*.json")
    
    if not json_files:
        console.print("[yellow]No state_*.json files found. Nothing to migrate.[/yellow]\n")
        return
    
    console.print(f"[green]Found {len(json_files)} file(s) to migrate:[/green]\n")
    
    for json_file in json_files:
        # Générer le nom de la base SQLite
        base_name = os.path.splitext(json_file)[0]
        db_file = base_name + ".db"
        
        # Vérifier si déjà migré
        if os.path.exists(db_file):
            console.print(f"[yellow]⚠️  {json_file} → {db_file} already exists, skipping[/yellow]\n")
            continue
        
        # Migrer
        migrate_json_to_sqlite(json_file, db_file)
    
    console.print("[bold green]✅ All migrations completed![/bold green]\n")


def compare_json_sqlite(json_file: str, db_file: str):
    """
    Compare un fichier JSON avec sa version SQLite pour vérifier la migration
    """
    console.print(f"\n[cyan]Comparing {json_file} with {db_file}...[/cyan]\n")
    
    # Charger JSON
    with open(json_file, 'r') as f:
        json_data = json.load(f)
    
    # Charger SQLite
    state_manager = StateManager(db_file)
    db_data = state_manager.get_all_files()
    
    # Comparer
    json_keys = set(json_data.keys())
    db_keys = set(db_data.keys())
    
    if json_keys == db_keys:
        console.print(f"[green]✅ File count matches: {len(json_keys):,} files[/green]")
        
        # Vérifier quelques entrées
        sample_size = min(100, len(json_keys))
        mismatches = 0
        
        for key in list(json_keys)[:sample_size]:
            json_entry = json_data[key]
            db_entry = db_data[key]
            
            if (json_entry.get('size') != db_entry.get('size') or 
                json_entry.get('modify') != db_entry.get('modify')):
                mismatches += 1
        
        if mismatches == 0:
            console.print(f"[green]✅ Sample check passed (verified {sample_size} entries)[/green]")
        else:
            console.print(f"[yellow]⚠️  Found {mismatches} mismatches in sample[/yellow]")
    else:
        console.print(f"[red]❌ File count mismatch![/red]")
        console.print(f"[red]   JSON: {len(json_keys):,} | SQLite: {len(db_keys):,}[/red]")
        
        only_json = json_keys - db_keys
        only_db = db_keys - json_keys
        
        if only_json:
            console.print(f"[red]   Only in JSON: {len(only_json):,} files[/red]")
        if only_db:
            console.print(f"[red]   Only in DB: {len(only_db):,} files[/red]")
    
    console.print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "compare" and len(sys.argv) == 4:
            # Mode comparaison
            compare_json_sqlite(sys.argv[2], sys.argv[3])
        elif sys.argv[1] == "migrate" and len(sys.argv) == 4:
            # Migrer un fichier spécifique
            migrate_json_to_sqlite(sys.argv[2], sys.argv[3])
        else:
            console.print("[red]Usage:[/red]")
            console.print("  python migrate_state.py                    # Migrate all state_*.json files")
            console.print("  python migrate_state.py migrate <json> <db>  # Migrate specific file")
            console.print("  python migrate_state.py compare <json> <db>  # Compare JSON and SQLite")
    else:
        # Mode automatique : migrer tous les fichiers
        migrate_all_state_files()