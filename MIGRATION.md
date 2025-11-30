# Guide de Migration - CRM Web V2

Ce document explique comment migrer de l'ancien `app.py` monolithique vers la nouvelle architecture modulaire.

## 📋 Vue d'ensemble

### Ancien système
- Fichier unique `app.py` (4,106 lignes)
- Connexions SQLite manuelles
- Pas de séparation des responsabilités
- Risques de sécurité (injection SQL, credentials exposés)

### Nouveau système
- Architecture modulaire avec blueprints
- Couche DAO pour l'abstraction de données
- Configuration centralisée
- Sécurité renforcée
- Logging structuré

## 🔄 Plan de migration

La migration se fait en **2 phases** :

### Phase 1 : Coexistence ✅ (ACTUEL)
- **Nouveau code** créé parallèlement à l'ancien
- **Ancien app.py** reste fonctionnel
- **Tests** sur la nouvelle architecture
- **Pas de risque** pour la production actuelle

### Phase 2 : Migration progressive (À VENIR)
- Migration route par route vers les blueprints
- Tests de chaque route migrée
- Suppression progressive de l'ancien code

## 📁 État actuel du projet

### ✅ Créé (Nouvelle architecture)

```
app/
├── __init__.py              # Application factory
├── config.py                # Configuration centralisée
├── extensions.py            # Extensions Flask
├── models/                  # Couche DAO
│   ├── base.py
│   ├── agent.py
│   ├── client.py
│   └── journal.py
├── blueprints/
│   └── auth.py             # Authentification
├── utils/
│   ├── decorators.py       # Décorateurs d'auth
│   ├── validators.py       # Validation sécurisée
│   ├── helpers.py          # Fonctions utilitaires
│   └── logger.py           # Logging
└── services/               # (à développer)

run.py                       # Nouveau point d'entrée
.env.example                 # Template de configuration
.gitignore                   # Sécurisé
README.md                    # Documentation
ARCHITECTURE.md              # Documentation architecture
```

### 🔄 À migrer (Ancien code)

```
app.py                       # 4,106 lignes à refactorer
├── Routes auth             → blueprints/auth.py ✅ FAIT
├── Routes client           → blueprints/client.py ⏳ À FAIRE
├── Routes dashboard        → blueprints/dashboard.py ⏳ À FAIRE
├── Routes agent            → blueprints/agent.py ⏳ À FAIRE
├── Routes export           → blueprints/export.py ⏳ À FAIRE
├── Routes admin            → blueprints/admin.py ⏳ À FAIRE
├── Routes Aircall          → blueprints/api.py ou service ⏳ À FAIRE
└── SocketIO handlers       → app/__init__.py ✅ FAIT

humanitaire.py               # Module analytique → services/humanitaire_service.py
etl_incremental.py           # ETL → services/etl_service.py
currency_utils.py            # ✅ OK (peut rester ou intégrer dans utils/)
```

## 🚀 Comment utiliser le nouveau système

### Option 1 : Tester la nouvelle architecture

```bash
# 1. Copier .env.example vers .env
cp .env.example .env

# 2. Configurer .env avec vos valeurs
nano .env

# 3. Lancer avec le nouveau système
python run.py
```

**Note** : Pour le moment, seule l'authentification est migrée. Les autres routes retourneront des erreurs 404.

### Option 2 : Continuer avec l'ancien système

```bash
# Lancer l'ancien app.py (toujours fonctionnel)
python app.py
```

## 📝 Procédure de migration d'une route

Voici comment migrer une route de `app.py` vers un blueprint :

### Exemple : Migration de la route `/dashboard`

#### 1. Ancien code (app.py)

```python
@app.route('/dashboard')
def dashboard():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    # Connexion manuelle
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Requête SQL directe
    c.execute("SELECT * FROM clients WHERE campagne_id = 1")
    clients = c.fetchall()
    conn.close()

    return render_template('dashboard.html', clients=clients)
```

#### 2. Nouveau code (blueprints/dashboard.py)

```python
from flask import Blueprint, render_template, session
from app.models.client import ClientDAO
from app.utils.decorators import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)
dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/dashboard')
@login_required
def index():
    """Dashboard SFR."""
    try:
        # Utiliser le DAO
        from flask import current_app
        client_dao = ClientDAO(current_app.config['DB_PATH'])

        # Méthode sécurisée
        clients = client_dao.find_all(
            campagne_id=1,
            order_by='DATE_MODIF DESC',
            limit=100
        )

        # Statistiques
        stats = client_dao.get_statistics(campagne_id=1)

        logger.info(f"Dashboard consulté par {session['agent_nom']}")

        return render_template('dashboard.html',
                             clients=clients,
                             stats=stats)

    except Exception as e:
        logger.error(f"Erreur dashboard: {e}")
        flash("Erreur lors du chargement du dashboard", "danger")
        return redirect(url_for('auth.login'))
```

#### 3. Enregistrer le blueprint (app/__init__.py)

```python
def _register_blueprints(app: Flask) -> None:
    from app.blueprints.auth import auth_bp
    from app.blueprints.dashboard import dashboard_bp  # Ajouter

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)  # Enregistrer
```

### Avantages de la nouvelle approche

| Aspect | Ancien | Nouveau |
|--------|--------|---------|
| **Sécurité** | ❌ Injection SQL possible | ✅ Requêtes préparées |
| **Auth** | ❌ Check manuel session | ✅ Décorateur `@login_required` |
| **Connexions** | ❌ Manuelles, risque fuite | ✅ Context manager auto |
| **Logging** | ❌ `print()` | ✅ Logger structuré |
| **Erreurs** | ❌ Pas de gestion | ✅ Try/except + log |
| **Tests** | ❌ Difficile | ✅ Facile (DAO mockable) |
| **Maintenance** | ❌ Code dupliqué | ✅ Code réutilisable |

## 🔐 Checklist de sécurité pour migration

Avant de migrer une route, vérifier :

- [ ] ✅ Utiliser les DAO au lieu de SQL direct
- [ ] ✅ Utiliser `@login_required` ou `@role_required`
- [ ] ✅ Valider les entrées utilisateur (validators.py)
- [ ] ✅ Logger les opérations importantes
- [ ] ✅ Gérer les erreurs avec try/except
- [ ] ✅ Pas de credentials hardcodés
- [ ] ✅ Utiliser `current_app.config` pour la config
- [ ] ✅ Retourner des messages d'erreur génériques (pas de détails techniques)

## 📊 Ordre de migration recommandé

1. **✅ Auth** (fait)
   - Login, logout, session check

2. **⏳ Dashboard** (priorité haute)
   - Dashboard SFR
   - Dashboard VALANDRE
   - Dashboard Humanitaire

3. **⏳ Client** (priorité haute)
   - Formulaire création
   - Liste clients
   - Modification
   - Suppression
   - Historique

4. **⏳ Agent** (priorité moyenne)
   - Liste agents
   - Création
   - Modification
   - Profil

5. **⏳ Export** (priorité moyenne)
   - Export Excel
   - Export CSV
   - Export KPI

6. **⏳ Admin** (priorité basse)
   - ETL
   - Imports
   - Synchro

7. **⏳ API** (priorité basse)
   - API REST
   - Aircall integration

## 🧪 Tests après migration

Pour chaque route migrée :

```bash
# 1. Test fonctionnel manuel
python run.py
# Tester dans le navigateur

# 2. Test unitaire (exemple)
pytest tests/unit/test_blueprints/test_dashboard.py

# 3. Vérifier les logs
tail -f logs/crm.log

# 4. Vérifier pas d'erreur
# Consulter les logs pour toute erreur
```

## 🚨 Problèmes courants et solutions

### Problème 1 : Import error

```
ImportError: cannot import name 'dashboard_bp'
```

**Solution** : Vérifier que le blueprint est bien créé et enregistré dans `app/__init__.py`

### Problème 2 : Template not found

```
TemplateNotFound: dashboard.html
```

**Solution** : Vérifier le chemin dans `create_app()` :
```python
app = Flask(__name__, template_folder='../templates')
```

### Problème 3 : Database not found

```
sqlite3.OperationalError: unable to open database file
```

**Solution** : Vérifier `DB_PATH` dans `.env` et que le fichier existe

### Problème 4 : Session vide après login

```python
# Vérifier que session.permanent = True
session.permanent = True
```

## 📋 Checklist avant commit

Avant de committer du code migré :

- [ ] Code testé manuellement
- [ ] Pas d'erreur dans les logs
- [ ] Tests unitaires passent
- [ ] Documentation mise à jour
- [ ] Pas de credentials dans le code
- [ ] Pas de `print()` (utiliser logger)
- [ ] Code review par un pair

## 🔄 Rollback en cas de problème

Si un problème survient avec la nouvelle architecture :

```bash
# 1. Revenir à l'ancien système
git checkout app.py

# 2. Relancer avec l'ancien
python app.py

# 3. Analyser les logs
cat logs/crm.log

# 4. Fixer le problème
# ...

# 5. Retester
python run.py
```

## 📞 Support

En cas de questions sur la migration :

1. Consulter [ARCHITECTURE.md](ARCHITECTURE.md)
2. Consulter [README.md](README.md)
3. Vérifier les logs : `logs/crm.log`
4. Créer une issue GitHub
5. Contacter l'équipe technique

---

**Note importante** : La migration est **progressive** et **sécurisée**. L'ancien système reste fonctionnel tant que toutes les routes ne sont pas migrées.

**Dernière mise à jour** : 2025-11-30
