# Synergy FTP Tool 🚀

Un outil professionnel unifié pour le déploiement et la sauvegarde de vos projets via FTP, pensé pour le travail collaboratif et l'intégration Git.

## 🌟 Points Forts
- **Interactif** : L'outil vous guide pas à pas.
- **Incrémental** : Seuls les fichiers modifiés sont téléchargés ou envoyés.
- **Sécurisé** : Les identifiants sont gérés via `.env` et ignorés par Git.
- **Collaboratif** : Plusieurs personnes peuvent travailler sur le même projet distant.

## ⚙️ Installation

1. Clonez ce dépôt.
2. Installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
3. Préparez votre configuration :
   - Copiez `.env.example` vers `.env`.
   - Remplissez vos accès FTP dans le fichier `.env`.

## 🚀 Utilisation

Lancez simplement l'outil :
```bash
python nas_tool.py
```

### Scénarios d'utilisation

#### 1. Déploiement (Local -> FTP)
Vous avez un projet local dans `project/` et vous voulez l'envoyer sur le serveur.
- Choisissez l'option `1`.
- L'outil détectera automatiquement le nom du dossier et créera le dossier correspondant sur le FTP s'il n'existe pas.

#### 2. Sauvegarde (FTP -> Local)
Vous voulez récupérer la dernière version d'un projet du serveur vers votre PC ou votre NAS.
- Choisissez l'option `2`.
- Entrez le nom du projet tel qu'il apparaît sur le serveur.
- L'outil téléchargera uniquement les nouveaux fichiers.

## 🛠️ Collaboration & Git

- **.gitignore** : Le projet est pré-configuré pour ne jamais envoyer vos mots de passe (`.env`) ou vos fichiers locaux temporaires sur Git.
- **Multi-projets** : Vous pouvez gérer plusieurs projets avec le même script en changeant simplement le dossier local lors de l'exécution.

---
*Fait avec ❤️ pour simplifier vos sauvegardes.*
