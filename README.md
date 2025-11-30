# CRM Web V2

Application de gestion de relation client (CRM) multi-campagne développée avec Flask.

## 🚀 Fonctionnalités

- **Multi-campagne** : Support de plusieurs campagnes (SFR, VALANDRE, HUMANITAIRE)
- **Gestion des clients** : CRUD complet avec historique des modifications
- **Gestion des agents** : Authentification, rôles (admin, superviseur, agent)
- **Dashboards** : Statistiques et KPIs par campagne
- **Exports** : Excel et CSV pour les données clients et KPIs
- **Intégration Aircall** : Téléchargement et lecture des enregistrements d'appels
- **Chat temps réel** : Communication entre agents via SocketIO
- **Journal d'activité** : Suivi des connexions et modifications

## 📋 Prérequis

- Python 3.8+
- SQLite 3
- Node.js (pour Tailwind CSS)

## 🔧 Installation

### 1. Cloner le repository

```bash
git clone <repository-url>
cd CRM-Web-V2
```

### 2. Créer un environnement virtuel

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

### 3. Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### 4. Installer les dépendances Node.js (Tailwind CSS)

```bash
npm install
```

### 5. Configurer l'environnement

Copier `.env.example` vers `.env` et configurer les variables :

```bash
cp .env.example .env
```

Éditer `.env` avec vos valeurs :

```env
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
DEBUG=False

# Aircall API
AIRCALL_API_ID=your-api-id
AIRCALL_API_TOKEN=your-api-token

# Autres configurations...
```

**⚠️ IMPORTANT** : Ne JAMAIS committer le fichier `.env` avec des vraies credentials !

### 6. Initialiser la base de données

```bash
python db_schema.py
```

### 7. Créer un utilisateur admin

```bash
python set_admin.py
```

## 🏃 Lancement

### Mode développement

```bash
python run.py
```

L'application sera accessible sur `http://localhost:5000`

### Mode production

```bash
export FLASK_ENV=production
export SECRET_KEY="your-production-secret-key"
gunicorn -k eventlet -w 1 -b 0.0.0.0:5000 "app:create_app()"
```

## 📁 Structure du projet

```
CRM-Web-V2/
├── app/                          # Application principale
│   ├── __init__.py              # Factory pattern
│   ├── config.py                # Configuration
│   ├── extensions.py            # Extensions Flask
│   ├── models/                  # Couche DAO
│   │   ├── base.py             # BaseDAO
│   │   ├── agent.py            # AgentDAO
│   │   ├── client.py           # ClientDAO
│   │   └── journal.py          # JournalDAO
│   ├── blueprints/             # Routes organisées
│   │   ├── auth.py             # Authentification
│   │   ├── client.py           # CRUD clients
│   │   ├── dashboard.py        # Dashboards
│   │   ├── admin.py            # Administration
│   │   ├── agent.py            # Gestion agents
│   │   ├── export.py           # Exports
│   │   └── api.py              # API REST
│   ├── services/               # Business logic
│   ├── utils/                  # Utilitaires
│   │   ├── decorators.py       # @login_required, @role_required
│   │   ├── validators.py       # Validation des données
│   │   ├── helpers.py          # Fonctions utilitaires
│   │   └── logger.py           # Configuration logging
│   ├── templates/              # Templates Jinja2
│   └── static/                 # Assets (CSS, JS, images)
├── tests/                       # Tests
│   ├── unit/                   # Tests unitaires
│   └── integration/            # Tests d'intégration
├── logs/                        # Logs applicatifs
├── migrations/                  # Migrations DB
├── Data/                        # Données d'import
├── .env.example                 # Template configuration
├── .gitignore                  # Fichiers ignorés par Git
├── requirements.txt            # Dépendances Python
├── package.json                # Dépendances Node.js
├── run.py                      # Point d'entrée
├── db_schema.py                # Schéma de base de données
└── README.md                   # Ce fichier
```

Pour plus de détails sur l'architecture, voir [ARCHITECTURE.md](ARCHITECTURE.md)

## 🔐 Sécurité

### Authentification

- Mots de passe hashés avec bcrypt
- Protection CSRF activée
- Rate limiting sur les routes sensibles
- Sessions sécurisées

### Configuration sécurisée

- ✅ Fichier `.env` dans `.gitignore`
- ✅ Requêtes SQL préparées (protection contre injection SQL)
- ✅ Validation des entrées utilisateur
- ✅ Debug mode désactivé par défaut
- ✅ Logs d'audit complets

## 👥 Rôles

- **Admin** : Accès complet, gestion des agents et campagnes
- **Superviseur** : Gestion d'équipe, accès aux dashboards avancés
- **Agent** : CRUD clients, accès aux dashboards basiques

## 📊 Dashboards

- **SFR** : `/dashboard` - Statistiques campagne SFR
- **VALANDRE** : `/dashboard_valandre` - Statistiques VALANDRE
- **HUMANITAIRE** : `/dashboard_humanitaire` - Statistiques et KPIs humanitaire

## 🧪 Tests

```bash
# Lancer tous les tests
pytest

# Lancer avec couverture
pytest --cov=app tests/

# Lancer des tests spécifiques
pytest tests/unit/test_currency.py
```

## 📝 Logs

Les logs sont stockés dans `logs/crm.log` avec rotation automatique (10MB max, 10 fichiers).

Niveaux de log :
- `DEBUG` : Informations détaillées
- `INFO` : Événements généraux
- `WARNING` : Avertissements
- `ERROR` : Erreurs

Configuration via `.env` :

```env
LOG_LEVEL=INFO
LOG_FILE=logs/crm.log
```

## 🔄 Import de données

```bash
# Import incrémental
python etl_incremental.py

# Import humanitaire
python import_huma_to_db.py
```

## 🐛 Debugging

En cas de problème :

1. Vérifier les logs : `tail -f logs/crm.log`
2. Vérifier la configuration : `.env`
3. Vérifier la base de données : `sqlite3 crm_clients.db`

## 📚 Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Architecture détaillée
- [API.md](API.md) - Documentation API (à venir)
- [DEPLOYMENT.md](DEPLOYMENT.md) - Guide de déploiement (à venir)

## 🤝 Contribution

1. Créer une branche : `git checkout -b feature/ma-fonctionnalite`
2. Committer les changements : `git commit -m "Ajout de ma fonctionnalité"`
3. Push la branche : `git push origin feature/ma-fonctionnalite`
4. Créer une Pull Request

## 📜 Licence

Propriétaire - Tous droits réservés

## 👨‍💻 Auteurs

- Équipe de développement CRM Web V2

## 🆘 Support

Pour toute question ou problème :
- Créer une issue sur GitHub
- Contacter l'équipe technique
