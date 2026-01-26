Sauvegarde FTP incrémentale vers NAS Synology
1. Objectif
Ce projet fournit un script Python de sauvegarde incrémentale permettant de copier automatiquement le contenu d’un serveur FTP distant (OVH) vers un NAS Synology.
La sauvegarde est dite incrémentale : lors de chaque exécution, seuls les nouveaux fichiers ou les fichiers modifiés sont téléchargés, afin d’optimiser le temps d’exécution et la bande passante.

2. Périmètre de la solution
Inclus
Connexion à un serveur FTP distant
Parcours récursif des dossiers
Téléchargement incrémental des fichiers
Conservation d’un état local des sauvegardes
Compatible avec une exécution planifiée sur NAS Synology
Non inclus
Suppression automatique des fichiers supprimés côté FTP
Chiffrement des données (à gérer au niveau du NAS si nécessaire)

3. Prérequis
Côté serveur FTP
Accès FTP valide (hôte, port, utilisateur, mot de passe)
Droits de lecture sur les fichiers à sauvegarder
Côté NAS Synology
Accès à un dossier de stockage (droits d’écriture)
Python 3.9 ou supérieur installé
Accès au planificateur de tâches (Task Scheduler)
⚠️ Un accès administrateur complet au NAS n’est pas requis.

4. Arborescence du projet
ftp_backup/├── backup.py          # Script principal├── config.yaml        # Configuration FTP et sauvegarde├── state.json         # État des sauvegardes (généré automatiquement)├── logs/│   └── backup.log     # Journal d’exécution└── data/    └── (données sauvegardées)

5. Configuration
Fichier config.yaml
ftp:  host: ftp.example.com  port: 21  user: ftp_user  password: ftp_password  remote_root: /backup:  local_root: ./data  state_file: ./state.json
Points importants
Les identifiants FTP doivent être corrects et actifs
Le dossier local_root doit être accessible en écriture

6. Fonctionnement de la sauvegarde incrémentale
À chaque exécution, le script : 1. Se connecte au serveur FTP 2. Liste récursivement tous les fichiers 3. Compare chaque fichier avec l’état précédent (state.json) selon : - le chemin du fichier - la taille - la date de modification 4. Télécharge uniquement les fichiers nouveaux ou modifiés 5. Met à jour le fichier d’état

7. Première exécution
python backup.py
Résultat attendu : - Tous les fichiers FTP sont téléchargés - Le fichier state.json est créé

8. Exécutions suivantes
Aucun fichier inchangé n’est retéléchargé
Les nouveaux fichiers sont ajoutés
Les fichiers modifiés sont mis à jour

9. Automatisation sur NAS Synology
Étapes recommandées
Copier le dossier ftp_backup sur le NAS
Ouvrir Panneau de configuration → Planificateur de tâches
Créer une Tâche planifiée → Script défini par l’utilisateur
Commande :
/usr/bin/python3 /chemin/vers/ftp_backup/backup.py
Planifier l’exécution (ex. quotidienne, nocturne)

10. Journalisation et suivi
Les journaux sont enregistrés dans le dossier logs/
Ils permettent de :
vérifier le bon déroulement des sauvegardes
identifier rapidement les erreurs de connexion ou de fichiers

11. Sécurité et bonnes pratiques
Ne pas partager le fichier config.yaml
Restreindre les droits du compte FTP à la lecture seule
Limiter l’accès au dossier de sauvegarde sur le NAS
Tester régulièrement la restauration des fichiers

12. Limites connues
FTP ne permet pas toujours une détection parfaite des modifications fines
Les suppressions côté FTP ne sont pas répliquées automatiquement
Ces limitations sont inhérentes au protocole FTP.

13. Support et évolutions possibles
La solution peut être étendue pour : - Ajouter le FTPS/SFTP - Gérer la suppression synchronisée - Ajouter des notifications (email) - Mettre en place un chiffrement

14. Conclusion
Cette solution fournit une sauvegarde fiable, automatisée et optimisée entre un serveur FTP distant et un NAS Synology, sans nécessiter d’accès administrateur complet au NAS.
Elle est adaptée à un usage professionnel et peut être intégrée facilement dans une stratégie de sauvegarde existante.

