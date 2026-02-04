# Synergy FTP Tool v3.0 - Guide d'utilisation

## Installation

```bash
pip install -r requirements.txt
```

Configurer le fichier `.env` avec vos identifiants :
```
FTP_HOST=ftp.example.com
FTP_PORT=22
FTP_USER=your_username
FTP_PASSWORD=your_password
FTP_REMOTE_ROOT=/
```

---

## Mode interactif (menu)

```bash
python nas_tool.py
```

---

## Mode CLI (lancement direct)

### Commande rapide

```bash
python nas_tool.py -target "../backup" -distant_folder "." -speed 3
```

Cela lance directement un **backup optimise** sans passer par le menu.

### Commande complete

```bash
python nas_tool.py -target "../backup" -distant_folder "/www/PACKAGE" -ignore_log_cache_temp 0 -verify_integrity 1 -speed 3 -auto_increment 1 -handle_deleted 1
```

### Parametres disponibles

| Parametre | Valeurs | Defaut | Description |
|---|---|---|---|
| `-target` | chemin | **requis** | Dossier local ou sauvegarder le backup |
| `-distant_folder` | chemin | **requis** | Dossier distant sur le serveur |
| `-speed` | 1, 2, 3, 4 | 2 | Vitesse (voir tableau ci-dessous) |
| `-ignore_log_cache_temp` | 0 ou 1 | 1 | Exclure les fichiers cache/logs/tmp |
| `-verify_integrity` | 0 ou 1 | 1 | Verifier l'integrite des fichiers apres download |
| `-auto_increment` | 0 ou 1 | 1 | Utiliser le scan incremental (plus rapide) |
| `-handle_deleted` | 0 ou 1 | 1 | Gerer les fichiers supprimes sur le serveur |

### Presets de vitesse (-speed)

**SFTP (port 22) :**

| Speed | Workers | Usage |
|---|---|---|
| 1 | 2 | Connexion lente |
| 2 | 3 | Normal (recommande) |
| 3 | 5 | Connexion rapide |
| 4 | 5 | Maximum SFTP |

**FTP (port 21) :**

| Speed | Workers | Usage |
|---|---|---|
| 1 | 5 | ADSL (< 10 Mbps) |
| 2 | 10 | Fibre grand public |
| 3 | 20 | Fibre pro |
| 4 | 30 | Datacenter |

Pour forcer un nombre precis de workers : `--workers 8`

---

## Exemples concrets

### Backup rapide d'un site web
```bash
python nas_tool.py -target "./backup_site" -distant_folder "." -speed 2
```

### Backup complet sans exclusions
```bash
python nas_tool.py -target "./backup_full" -distant_folder "/" -ignore_log_cache_temp 0 -speed 3
```

### Backup sans verification (plus rapide)
```bash
python nas_tool.py -target "./backup" -distant_folder "." -verify_integrity 0 -speed 3
```

### Backup classique (non optimise)
```bash
python nas_tool.py backup --local "./backup" --remote "."
```

### Deploy (local vers serveur)
```bash
python nas_tool.py deploy -target "./project" -distant_folder "mon_site" --dry-run
```

---

## Aide

```bash
python nas_tool.py --help
```
