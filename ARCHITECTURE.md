# Architecture CRM Web V2

Ce document décrit l'architecture de l'application CRM Web V2 après refactoring.

## 📐 Vue d'ensemble

L'application suit une architecture **MVC (Model-View-Controller)** avec le pattern **Factory** pour Flask et une **couche DAO (Data Access Object)** pour l'abstraction de la base de données.

### Principes architecturaux

1. **Séparation des responsabilités** : Chaque module a un rôle bien défini
2. **Sécurité par design** : Requêtes préparées, validation des entrées, logging
3. **Modularité** : Blueprints pour organiser les routes par domaine
4. **Testabilité** : Code découplé facile à tester
5. **Maintenabilité** : Code organisé, documenté et évolutif

## 🏗️ Structure détaillée

```
CRM-Web-V2/
├── app/                          # Application Flask
│   ├── __init__.py              # Application Factory
│   ├── config.py                # Configuration centralisée
│   ├── extensions.py            # Extensions Flask (SocketIO, Limiter)
│   │
│   ├── models/                  # Couche DAO (Data Access Objects)
│   │   ├── __init__.py
│   │   ├── base.py             # BaseDAO avec méthodes communes
│   │   ├── agent.py            # AgentDAO - gestion des agents
│   │   ├── client.py           # ClientDAO - gestion des clients
│   │   └── journal.py          # JournalDAO - logs et historique
│   │
│   ├── blueprints/             # Routes organisées par domaine
│   │   ├── __init__.py
│   │   ├── auth.py             # Authentification (login, logout)
│   │   ├── client.py           # CRUD clients
│   │   ├── dashboard.py        # Dashboards et statistiques
│   │   ├── admin.py            # Administration (ETL, imports)
│   │   ├── agent.py            # Gestion des agents
│   │   ├── export.py           # Exports Excel/CSV
│   │   └── api.py              # API REST
│   │
│   ├── services/               # Business logic (à développer)
│   │   ├── __init__.py
│   │   ├── client_service.py  # Logique métier clients
│   │   ├── agent_service.py   # Logique métier agents
│   │   ├── etl_service.py     # ETL et imports
│   │   └── aircall_service.py # Intégration Aircall
│   │
│   ├── utils/                  # Utilitaires
│   │   ├── __init__.py
│   │   ├── decorators.py       # @login_required, @role_required
│   │   ├── validators.py       # Validation et sanitization
│   │   ├── helpers.py          # Fonctions utilitaires
│   │   └── logger.py           # Configuration logging
│   │
│   ├── templates/              # Templates Jinja2
│   │   ├── base.html           # Template de base
│   │   ├── login.html
│   │   ├── dashboard*.html
│   │   ├── formulaire*.html
│   │   └── errors/             # Pages d'erreur
│   │       ├── 403.html
│   │       ├── 404.html
│   │       └── 500.html
│   │
│   └── static/                 # Assets statiques
│       ├── css/
│       ├── js/
│       └── uploads/
│
├── tests/                       # Tests
│   ├── conftest.py             # Configuration pytest
│   ├── unit/                   # Tests unitaires
│   │   ├── test_models/
│   │   ├── test_utils/
│   │   └── test_services/
│   ├── integration/            # Tests d'intégration
│   │   └── test_blueprints/
│   └── fixtures/               # Données de test
│
├── logs/                        # Logs applicatifs
│   └── crm.log
│
├── migrations/                  # Migrations de base de données
│
├── Data/                        # Données d'import
│   ├── inbox/
│   └── archive/
│
├── .env.example                 # Template de configuration
├── .env                        # Configuration (IGNORÉ par Git)
├── .gitignore                  # Fichiers ignorés
├── requirements.txt            # Dépendances Python
├── package.json                # Dépendances Node.js
├── run.py                      # Point d'entrée
├── db_schema.py                # Initialisation du schéma DB
└── README.md                   # Documentation principale
```

## 🔄 Flux de données

### 1. Requête HTTP

```
Client → Route (Blueprint) → Service (Business Logic) → DAO (Data Access) → Database
                ↓                       ↓                      ↓
            Validation           Logging                Context Manager
```

### 2. Exemple : Login

```python
# 1. Route (blueprints/auth.py)
@auth_bp.route('/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    login_input = request.form.get('login')
    password = request.form.get('password')

    # 2. DAO (models/agent.py)
    agent_dao = AgentDAO(db_path)
    agent = agent_dao.authenticate(login_input, password)

    if agent:
        # 3. Session
        session['agent_nom'] = agent['NOM']
        # ...
        return redirect(url_for('dashboard.index'))
```

## 🗄️ Couche DAO (Data Access Object)

### Principe

La couche DAO abstrait complètement l'accès à la base de données et fournit :

- **Gestion sécurisée des connexions** (context managers)
- **Requêtes préparées** (protection contre injection SQL)
- **Méthodes CRUD génériques** (find, create, update, delete)
- **Méthodes métier spécifiques** par entité

### BaseDAO

Classe de base fournissant les méthodes communes :

```python
class BaseDAO:
    def __init__(self, db_path: str)
    def get_connection() -> Connection          # Context manager
    def execute_query(query, params) -> Any     # Exécution sécurisée
    def find_by_id(table, id) -> Dict
    def find_all(table, where, params) -> List[Dict]
    def count(table, where, params) -> int
    def insert(table, data) -> int
    def update(table, data, where, params) -> int
    def delete(table, where, params) -> int
```

### DAO spécifiques

#### AgentDAO

```python
class AgentDAO(BaseDAO):
    def find_by_login(login) -> Optional[Dict]
    def authenticate(login, password) -> Optional[Dict]
    def create(data) -> int                    # Hash automatique du MDP
    def update_agent(agent_id, data) -> int
    def delete_agent(agent_id) -> int
    def login_exists(login) -> bool
```

#### ClientDAO

```python
class ClientDAO(BaseDAO):
    def find_by_id(client_id, table) -> Optional[Dict]
    def find_all(table, campagne_id, agent, statut, search) -> List[Dict]
    def count_clients(...) -> int
    def create(data, table) -> int
    def update_client(client_id, data, table) -> int
    def delete_client(client_id, table) -> int
    def find_by_phone(phone, table) -> Optional[Dict]
    def get_statistics(table, campagne_id) -> Dict
    def detect_table_for_client(client_id) -> Optional[str]
```

#### JournalDAO

```python
class JournalDAO(BaseDAO):
    # Journal de connexions
    def log_connexion(agent_nom, page, type_event) -> int
    def get_connexions(...) -> List[Dict]
    def count_connexions(...) -> int
    def get_presence_stats(date) -> List[Dict]

    # Historique des modifications
    def log_modification(client_id, agent, champ, ancienne, nouvelle) -> int
    def log_modifications_batch(client_id, agent, modifications) -> int
    def get_historique_client(client_id) -> List[Dict]
    def get_historique_agent(agent) -> List[Dict]
```

## 🎯 Blueprints

Les blueprints organisent les routes par domaine fonctionnel.

### auth_bp (Authentification)

```python
GET/POST /login           # Connexion
GET      /logout          # Déconnexion
GET      /is_logged_in    # Vérification session (API)
```

### client_bp (Gestion clients) - À implémenter

```python
GET      /                        # Liste des clients
GET/POST /formulaire              # Nouveau client SFR
GET/POST /formulaire_valandre     # Nouveau client VALANDRE
GET/POST /modifier_client/<id>    # Modification
POST     /supprimer_client/<id>   # Suppression
GET      /historique_client/<id>  # Historique
```

### dashboard_bp (Dashboards) - À implémenter

```python
GET /dashboard                    # Dashboard SFR
GET /dashboard_valandre           # Dashboard VALANDRE
GET /dashboard_humanitaire        # Dashboard HUMANITAIRE
GET /dashboard_ca_projets         # Projections CA
```

### agent_bp (Gestion agents) - À implémenter

```python
GET/POST /parametres              # Créer agent (admin/superviseur)
GET/POST /profil                  # Profil de l'agent
GET/POST /modifier_agent/<id>     # Modifier agent
POST     /supprimer_agent/<id>    # Supprimer agent
GET      /classement_agents       # Classement
GET      /live_agents             # Activité temps réel
```

### export_bp (Exports) - À implémenter

```python
GET /export_excel_sfr             # Export Excel SFR
GET /export_excel_valandre        # Export Excel VALANDRE
GET /export_excel_humanitaire     # Export Excel Humanitaire
GET /export_journal               # Export journal
GET /export_kpi_csv               # Export KPI CSV
GET /export_kpi_xlsx              # Export KPI Excel
```

### admin_bp (Administration) - À implémenter

```python
POST /run_etl                     # Lancer ETL
POST /refresh_import              # Rafraîchir import
POST /import_primes_huma          # Import primes humanitaire
POST /huma_sync_agents            # Synchro agents humanitaire
POST /backfill_call_ids           # Mise à jour call IDs Aircall
```

## 🔒 Sécurité

### 1. Configuration sécurisée

- `.env` **jamais** versionné
- Clés API dans variables d'environnement
- `SECRET_KEY` unique par environnement
- Debug mode **désactivé** en production

### 2. Authentification

```python
# Utilisation des décorateurs
@login_required
def protected_route():
    ...

@role_required(['admin'])
def admin_route():
    ...

@admin_or_superviseur_required
def management_route():
    ...
```

### 3. Protection contre injection SQL

```python
# ❌ MAUVAIS (vulnérable)
query = f"SELECT * FROM clients WHERE nom = '{nom}'"

# ✅ BON (sécurisé)
query = "SELECT * FROM clients WHERE nom = ?"
dao.execute_query(query, (nom,))
```

### 4. Validation des entrées

```python
from app.utils.validators import (
    is_valid_phone,
    is_valid_email,
    sanitize_table_name,
    sanitize_column_name
)

# Valider avant d'utiliser
if is_valid_phone(phone):
    client_dao.find_by_phone(phone)

# Sanitiser les noms de table/colonne
table = sanitize_table_name(table_input)
```

### 5. Rate Limiting

```python
@limiter.limit("5 per minute;20 per hour")
def login():
    ...
```

### 6. Logging d'audit

Toutes les opérations sensibles sont loggées :

- Connexions/déconnexions
- Modifications de clients
- Créations/suppressions d'agents
- Erreurs d'authentification

## 📊 Configuration

### config.py

Trois environnements :

1. **Development** : Debug activé, logs verbeux
2. **Production** : Debug désactivé, sécurité renforcée
3. **Testing** : Base de données séparée, fixtures

```python
# Utilisation
config_class = get_config()  # Détecte FLASK_ENV
app.config.from_object(config_class)
```

### Variables d'environnement (.env)

```env
# Application
FLASK_ENV=development|production|testing
SECRET_KEY=your-secret-key
DEBUG=False

# Base de données
DB_NAME=crm_clients.db
HUMA_DB_NAME=humanitaire.db

# API externes
AIRCALL_API_ID=xxx
AIRCALL_API_TOKEN=xxx

# Logging
LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
LOG_FILE=logs/crm.log
```

## 🧪 Tests

### Structure

```
tests/
├── unit/              # Tests unitaires (DAO, utils, services)
├── integration/       # Tests d'intégration (blueprints, API)
└── fixtures/          # Données de test
```

### Exemple de test

```python
# tests/unit/test_models/test_agent_dao.py
import pytest
from app.models.agent import AgentDAO

def test_authenticate_success(test_db):
    dao = AgentDAO(test_db)
    agent = dao.authenticate('admin', 'password123')
    assert agent is not None
    assert agent['LOGIN'] == 'admin'

def test_authenticate_failure(test_db):
    dao = AgentDAO(test_db)
    agent = dao.authenticate('admin', 'wrong_password')
    assert agent is None
```

## 📝 Logging

### Configuration

```python
# app/utils/logger.py
def setup_logging(app):
    # Handler fichier avec rotation (10MB, 10 fichiers)
    # Handler console
    # Formatage : [timestamp] LEVEL in module (function:line): message
```

### Utilisation

```python
from app.utils.logger import get_logger

logger = get_logger(__name__)

logger.debug("Information de débogage")
logger.info("Événement normal")
logger.warning("Avertissement")
logger.error("Erreur")
logger.critical("Erreur critique")
```

## 🚀 Évolutions futures

### Phase 1 : Compléter la migration (En cours)
- ✅ Configuration centralisée
- ✅ Logging structuré
- ✅ Couche DAO
- ✅ Décorateurs d'authentification
- ✅ Blueprint auth
- ⏳ Autres blueprints (client, dashboard, agent, export, admin)

### Phase 2 : Améliorer la qualité
- Augmenter la couverture de tests (objectif 80%)
- Ajouter tests d'intégration
- Documentation API (OpenAPI/Swagger)
- CI/CD avec GitHub Actions

### Phase 3 : Optimisations
- Migration vers ORM (SQLAlchemy)
- Caching (Redis)
- Queue de tâches (Celery)
- API GraphQL

### Phase 4 : Fonctionnalités avancées
- Notifications en temps réel
- Export PDF
- Dashboard analytique avancé
- Application mobile (API REST)

## 📚 Bonnes pratiques

1. **Toujours** utiliser les DAO pour accéder aux données
2. **Toujours** valider les entrées utilisateur
3. **Toujours** logger les opérations importantes
4. **Toujours** utiliser des requêtes préparées
5. **Jamais** hardcoder des credentials
6. **Jamais** activer debug=True en production
7. **Jamais** versionner .env

## 🆘 Debugging

### Problème de connexion
```bash
# Vérifier les logs
tail -f logs/crm.log

# Vérifier la session
flask shell
>>> from flask import session
>>> session
```

### Problème de base de données
```bash
# Explorer la DB
sqlite3 crm_clients.db
.tables
.schema agents
SELECT * FROM agents LIMIT 5;
```

### Problème de configuration
```python
# Dans l'application
print(app.config)
```

---

**Dernière mise à jour** : 2025-11-30
**Version** : 2.0 (Refactoring architectural)
