# CLAUDE.md - CRM Web V2 Project Guide

## Project Overview

**CRM-Web-V2** is a Flask-based Customer Relationship Management (CRM) web application designed for call center operations, with specialized support for multiple campaigns including:
- **EXOSPHERE_SFR**: Traditional telecom sales campaign
- **VALANDRE**: Multi-product sales campaign (STRATO, LSR, PRESSE, ENI, SERENITY, PROTEC_ALLIANCE, WEKIWI)
- **HUMANITAIRE**: Humanitarian donation campaigns with complex metrics and prime calculations

The application features real-time communication via WebSockets, role-based access control, Aircall API integration, ETL data pipelines, and comprehensive reporting dashboards.

---

## Tech Stack

### Backend
- **Flask 3.1.2**: Web framework
- **Flask-SocketIO 5.5.1**: Real-time bidirectional communication
- **Flask-WTF 1.2.2**: CSRF protection and form handling
- **Flask-Limiter 3.12**: Rate limiting
- **bcrypt 4.3.0**: Password hashing
- **SQLite3**: Database (no ORM, raw SQL queries)

### Frontend
- **TailwindCSS 4.1.14**: Utility-first CSS framework
- **Jinja2 3.1.6**: Template engine
- **JavaScript**: Client-side interactivity and AJAX

### Data Processing
- **Pandas 2.3.2**: Data manipulation and ETL
- **NumPy 2.3.2**: Numerical operations
- **openpyxl 3.1.5**: Excel file handling

### Testing
- **pytest 8.4.2**: Testing framework

### External Integrations
- **Aircall API**: Call center phone system integration
- **HTTP Basic Auth**: API authentication

---

## Directory Structure

```
CRM-Web-V2/
├── app.py                          # Main Flask application (4106 lines)
├── db_schema.py                    # Database schema management (single source of truth)
├── humanitaire.py                  # Humanitarian campaign logic (875 lines)
├── etl_incremental.py              # Incremental ETL pipeline (546 lines)
├── currency_utils.py               # Multi-currency formatting utilities
├── requirements.txt                # Python dependencies
├── package.json                    # Node.js dependencies (TailwindCSS)
├── tailwind.config.js              # TailwindCSS configuration
│
├── templates/                      # Jinja2 HTML templates
│   ├── base.html                   # Base template with common layout
│   ├── login.html                  # Authentication page
│   ├── dashboard.html              # Main CRM dashboard
│   ├── dashboard_valandre.html     # VALANDRE campaign dashboard
│   ├── dashboard_humanitaire.html  # Humanitarian campaign dashboard
│   ├── clients_list.html           # Client list view
│   ├── formulaire.html             # Client creation form
│   ├── modifier_client.html        # Client editing form
│   ├── historique_client.html      # Client history view
│   ├── journal.html                # Activity journal
│   ├── journal_presence.html       # Presence tracking
│   ├── profil_agent.html           # Agent profile
│   ├── classement_agents.html      # Agent rankings
│   ├── live_agents.html            # Real-time agent status
│   ├── parametres.html             # Settings page
│   └── overview.html               # Overview/stats page
│
├── static/                         # Static assets
│   ├── css/
│   │   └── tailwind.css            # Compiled TailwindCSS
│   └── uploads/                    # User-uploaded files (logos, avatars)
│
├── tests/                          # Test suite
│   ├── conftest.py                 # pytest configuration
│   └── test_currency.py            # Currency utilities tests
│
├── scripts/                        # Utility scripts
│   └── utils_normalize.py          # Data normalization utilities
│
├── Data/                           # Data directory (gitignored)
│   ├── inbox/                      # Incoming files for ETL
│   └── archive/                    # Processed files archive
│
├── .env                            # Environment variables (gitignored)
├── .gitignore                      # Git ignore patterns
│
└── Standalone scripts (root level):
    ├── etl_import.py               # Initial data import
    ├── build_warehouse.py          # Data warehouse construction
    ├── create_huma_db.py           # Humanitarian database setup
    ├── import_huma_to_db.py        # Humanitarian data import
    ├── create_index_and_views.py   # Database optimization
    ├── dedupe_*.py                 # Deduplication utilities
    ├── check_*.py                  # Data validation scripts
    ├── map_clients_campaigns.py    # Campaign mapping
    ├── set_admin.py                # Admin user setup
    └── fix_admin.py                # Admin user fixes
```

---

## Database Schema

### Primary Database: `crm.db` (or path specified in .env)

The database schema is managed by **`db_schema.py`**, which provides three main functions:

#### 1. `ensure_crm_schema(db_path)` - Main CRM Tables

**`clients` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
DATE_SIGNATURE TEXT NOT NULL
CIVILITE_CLIENT TEXT
NOM_CLIENT TEXT
PRENOM_CLIENT TEXT
TELEPHONE TEXT
STATUT TEXT                   -- Status: Signé, Attente, Refus, etc.
AGENT TEXT                    -- Agent name
DEUXIEME_ADRESSE TEXT
TROISIEME_ADRESSE TEXT
TYPE_OFFRE TEXT
CREE_PAR TEXT
MODIFIE_PAR TEXT
DATE_MODIF TEXT
campagne_id INTEGER DEFAULT 1

-- Extended columns (added dynamically):
TITRE TEXT
NOM_VENDEUR TEXT
PRENOM_VENDEUR TEXT
TELEPHONE_VENDEUR TEXT
N_CONTRAT TEXT
N_REFERENCE TEXT
VALIDATION_PRODUIT[1-3] TEXT
STATUT_PRODUIT[1-3] TEXT
EXTRANET TEXT
CALL_ID TEXT                  -- Aircall call ID

-- VALANDRE product columns (for each product):
{PRODUCT}_NUM TEXT
{PRODUCT}_STATUT TEXT
{PRODUCT}_REMARQUE TEXT
-- Products: STRATO, LSR, PRESSE, ENI, SERENITY, PROTEC_ALLIANCE, WEKIWI
```

**`agents` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
NOM TEXT NOT NULL UNIQUE
LOGIN TEXT NOT NULL UNIQUE
MDP TEXT NOT NULL            -- bcrypt hashed password
ROLE TEXT DEFAULT 'agent'    -- Roles: 'admin', 'superviseur', 'agent'
campagne_id INTEGER DEFAULT 1
PAYS_CODE TEXT               -- Country code (FR, TN, SN, etc.)
photo TEXT                   -- Avatar path
TV TEXT                      -- Team/TV assignment
-- Index: idx_agents_tv
```

**`journal_connexions` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
agent_nom TEXT NOT NULL
date_connexion TEXT NOT NULL
page TEXT
type_event TEXT DEFAULT 'connexion'
```

**`historique_clients` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
client_id INTEGER
date_modif TEXT
agent TEXT
champ_modifie TEXT
ancienne_valeur TEXT
nouvelle_valeur TEXT
```

**`campagnes` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
nom TEXT NOT NULL
type_export TEXT NOT NULL    -- 'simple' or 'special'

-- Default campaigns:
-- (1, 'EXOSPHERE_SFR', 'simple')
-- (2, 'VALANDRE', 'special')
-- (3, 'HUMANITAIRE', 'simple')
```

#### 2. `ensure_primes_table(db_path)` - Humanitarian Primes

**`primes_huma` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
dons_cible INTEGER NOT NULL
don_moyen_cible INTEGER NOT NULL
prime_eur REAL NOT NULL
prime_dt REAL NOT NULL
-- Index: idx_primes_cible
```

#### 3. `ensure_huma_schema(db_path)` - Humanitarian Database

Separate database for humanitarian campaigns (e.g., `Data/humanitaire.db`):

**`calls` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
base TEXT                    -- Campaign base name
agent TEXT
call_date TEXT
is_cu INTEGER DEFAULT 0      -- Is contact utile
is_don INTEGER DEFAULT 0     -- Is donation
is_donmail INTEGER DEFAULT 0 -- Is donation by mail
is_indecis INTEGER DEFAULT 0 -- Is indecisive
montant REAL DEFAULT 0.0
-- Indexes: idx_calls_date, idx_calls_agent, idx_calls_base
```

**`grh_hours` table**:
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
jour TEXT                    -- Date
agent TEXT
heures REAL DEFAULT 0.0      -- Hours worked
-- Indexes: idx_grh_jour, idx_grh_agent
```

**`objectifs` table**:
```sql
agent TEXT PRIMARY KEY
OBJECTIF_DONS INTEGER
DON_MOYEN_CIBLE REAL
PRIME_BASE_EUR REAL
```

---

## Key Modules & Files

### Core Application Files

#### `app.py` (4106 lines)
The main Flask application containing:
- **Flask app initialization**: CSRF protection, SocketIO, rate limiting
- **Authentication & Authorization**: Login system with bcrypt, role-based access control
- **Routes**: 50+ routes for all CRM operations
- **Aircall Integration**: Phone number normalization, call tagging
- **Database Operations**: Direct SQLite queries (no ORM)
- **Excel Export**: Dynamic report generation
- **WebSocket Handlers**: Real-time agent status updates

**Key Route Patterns**:
- `/` - Login page
- `/dashboard` - Main dashboard (redirects based on campaign)
- `/dashboard_valandre` - VALANDRE campaign dashboard
- `/dashboard_humanitaire` - Humanitarian campaign dashboard
- `/clients_list` - Client list with filtering
- `/formulaire` - New client form
- `/modifier_client/<id>` - Edit client
- `/historique_client/<id>` - Client history
- `/journal` - Activity journal
- `/profil_agent/<nom>` - Agent profile
- `/classement_agents` - Agent rankings
- `/parametres` - Settings
- `/api/*` - API endpoints for AJAX requests

#### `db_schema.py` (273 lines)
**Single source of truth** for database schema. Consolidates schema management that was previously duplicated across multiple files.

**Key Functions**:
- `ensure_crm_schema(db_path)`: Creates/migrates main CRM tables
- `ensure_primes_table(db_path)`: Creates humanitarian primes table
- `ensure_huma_schema(db_path)`: Creates humanitarian database tables
- `_ensure_columns(cursor, table, column_definitions)`: Dynamic column addition

**Design Principles**:
- **Idempotent**: Can be called multiple times safely
- **Non-destructive**: Only adds missing tables/columns, never drops
- **Context managers**: Guarantees connection cleanup

### Data Processing Modules

#### `etl_incremental.py` (546 lines)
Incremental ETL pipeline for daily data imports.

**Features**:
- **Auto-detection**: Handles CSV and Excel files automatically
- **Encoding detection**: Uses chardet for robust CSV parsing
- **Deduplication**: SHA1 file hashing + row-level deduplication
- **Import tracking**: `import_log` table prevents re-processing
- **Status normalization**: Robust detection of donation types (don, don_mail, indécis, refus)
- **Data validation**: Logs invalid amounts and skipped rows

**Key Functions**:
- `ensure_schema_incremental(db_path)`: Schema migration with backfill
- `import_appels_incremental(db_path, folder, date_filter)`: Import call data
- `import_grh_incremental(db_path, folder)`: Import work hours data

**Usage**:
```bash
python etl_incremental.py --db Data/humanitaire.db --appels Data/inbox --date-filter 2025-11-01
```

#### `humanitaire.py` (875 lines)
Business logic for humanitarian campaigns.

**Key Functions**:
- `generer_dashboard_humanitaire_df(...)`: Generates comprehensive agent statistics
- `export_dashboard_humanitaire_xlsx(...)`: Excel export with formatting
- `extract_tv_list(db_huma)`: Extract team/TV assignments
- `list_bases_disponibles(db_huma)`: List available campaign bases
- `calculer_chiffre_affaire_dyn(...)`: Dynamic turnover calculation from Excel matrix

**Metrics Calculated**:
- Donations count and average
- Contact rate (CU%)
- Donation rate
- Hours worked
- Prime calculations (EUR/TND)
- Turnover

#### `currency_utils.py` (171 lines)
Multi-currency formatting utilities.

**Key Functions**:
- `load_countries()`: Load country/currency data from `countries.json`
- `format_local_amount(montant, pays_code)`: Format amount with local currency
- `get_country_by_code(code)`: Get country data by code

**Supported Currencies**:
- EUR (France): `1234.56 €`
- TND (Tunisie): `1234.56 TND`
- XOF (Sénégal): `1234.56 XOF`
- DZD (Algérie): `1234.56 DZD`
- MUR (Île Maurice): `1234.56 MUR`

---

## Development Workflows

### Initial Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install Node.js dependencies (TailwindCSS)
npm install

# 3. Create .env file
cat > .env << EOF
AIRCALL_API_ID=your_api_id
AIRCALL_API_TOKEN=your_api_token
EOF

# 4. Initialize databases
python -c "from db_schema import ensure_crm_schema; ensure_crm_schema('crm.db')"
python -c "from db_schema import ensure_huma_schema; ensure_huma_schema('Data/humanitaire.db')"

# 5. Create admin user
python set_admin.py

# 6. Run the application
python app.py
```

### Database Initialization Pattern

Always use `db_schema.py` functions for database initialization:

```python
from db_schema import ensure_crm_schema, ensure_primes_table, ensure_huma_schema

# Main CRM database
ensure_crm_schema("crm.db")
ensure_primes_table("crm.db")

# Humanitarian database
ensure_huma_schema("Data/humanitaire.db")
```

**Never** create tables manually in `app.py` or scripts. Always use `db_schema.py`.

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_currency.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=. tests/
```

### CSS Development (TailwindCSS)

```bash
# Watch mode (development)
npx tailwindcss -i ./static/css/tailwind.css -o ./static/css/output.css --watch

# Build for production
npx tailwindcss -i ./static/css/tailwind.css -o ./static/css/output.css --minify
```

### Data Import Workflows

#### Initial Import
```bash
# Import initial client data
python etl_import.py --file data.xlsx

# Import humanitarian data
python import_huma_to_db.py --file humanitarian_data.xlsx
```

#### Incremental Daily Import
```bash
# Process new files in Data/inbox
python etl_incremental.py --db Data/humanitaire.db --appels Data/inbox --date-filter 2025-11-30

# Files are automatically moved to Data/archive after processing
```

---

## Key Conventions & Patterns

### Code Style

1. **Type Hints**: Use `from __future__ import annotations` and type hints
2. **Imports Organization**:
   - Standard library
   - Third-party packages
   - Internal modules
3. **String Formatting**: Use f-strings for readability
4. **SQL Queries**: Use parameterized queries to prevent SQL injection
   ```python
   # Good
   cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))

   # Bad
   cursor.execute(f"SELECT * FROM clients WHERE id = {client_id}")
   ```

### Database Access Patterns

1. **Always use context managers** or explicit try/finally for connections:
   ```python
   conn = sqlite3.connect(db_path)
   try:
       cursor = conn.cursor()
       # ... operations ...
       conn.commit()
   finally:
       conn.close()
   ```

2. **Use db_schema.py for schema changes**:
   - Never manually ALTER TABLE in app code
   - Add new columns to `db_schema.py` column definitions
   - Call appropriate `ensure_*_schema()` function

3. **Raw SQL preferred over ORM**:
   - This codebase intentionally uses raw SQL
   - SQLite3 cursor interface throughout
   - Direct control over queries for performance

### Authentication & Authorization

1. **Password Hashing**: Always use bcrypt
   ```python
   import bcrypt
   hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
   ```

2. **Session Management**:
   - `session['nom']`: Agent name
   - `session['role']`: Agent role (admin, superviseur, agent)
   - `session['campagne_id']`: Current campaign

3. **Role-Based Access**:
   ```python
   if session.get('role') != 'admin':
       return redirect(url_for('login'))
   ```

### Frontend Patterns

1. **AJAX Requests**: Use fetch API with CSRF tokens
   ```javascript
   fetch('/api/endpoint', {
       method: 'POST',
       headers: {
           'Content-Type': 'application/json',
           'X-CSRFToken': csrfToken
       },
       body: JSON.stringify(data)
   })
   ```

2. **TailwindCSS Classes**: Utility-first approach
   - Use predefined classes
   - Avoid custom CSS when possible
   - Maintain consistency across templates

3. **Template Inheritance**: Use `base.html` as parent
   ```jinja2
   {% extends "base.html" %}
   {% block content %}
   <!-- page content -->
   {% endblock %}
   ```

### Data Normalization

1. **Phone Numbers**: Always normalize before database operations
   ```python
   def _normalize_phone(num: str) -> str:
       return re.sub(r"[^\d+]", "", str(num or "")).strip()
   ```

2. **Text Fields**: Remove accents and normalize case
   ```python
   def normalize_text(s: str) -> str:
       return _strip_accents(s or "").lower().strip()
   ```

3. **Dates**: ISO format (YYYY-MM-DD) in database
   ```python
   def _to_iso(d: str | None, default: date) -> str:
       # Accepts 'YYYY-MM-DD' or 'DD/MM/YYYY'
       # Returns 'YYYY-MM-DD'
   ```

---

## Common Tasks for AI Assistants

### Adding a New Feature

1. **Read existing code first**: Never propose changes without reading relevant files
2. **Use db_schema.py for schema changes**: Add columns to appropriate function
3. **Follow existing patterns**: Match code style and patterns in the file
4. **Test database changes**: Ensure idempotent behavior
5. **Update routes in app.py**: Follow RESTful conventions
6. **Create/update templates**: Extend base.html, use TailwindCSS
7. **Run tests**: Ensure nothing breaks

### Adding a New Route

```python
@app.route('/new_route', methods=['GET', 'POST'])
def new_route():
    # Check authentication
    if 'nom' not in session:
        return redirect(url_for('login'))

    # Check authorization if needed
    if session.get('role') not in ['admin', 'superviseur']:
        flash("Accès refusé", "error")
        return redirect(url_for('dashboard'))

    # Handle request
    if request.method == 'POST':
        # Process form data
        pass

    return render_template('new_template.html')
```

### Adding a Database Column

1. **Edit db_schema.py**:
   ```python
   # In ensure_crm_schema(), add to client_columns dict:
   client_columns = {
       # ... existing columns ...
       "NEW_COLUMN": "NEW_COLUMN TEXT DEFAULT ''",
   }
   ```

2. **No migration file needed**: Function is idempotent
3. **Call ensure_crm_schema()** on next app startup
4. **Update forms/templates** to use new column

### Debugging Database Issues

```python
# Check if table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cursor.fetchall())

# Check table schema
cursor.execute("PRAGMA table_info(clients)")
print(cursor.fetchall())

# Check indexes
cursor.execute("PRAGMA index_list(agents)")
print(cursor.fetchall())
```

### Working with Campaigns

Each campaign has different requirements:

1. **EXOSPHERE_SFR** (campagne_id=1):
   - Simple client tracking
   - Standard export format
   - Basic status management

2. **VALANDRE** (campagne_id=2):
   - Multi-product tracking (7 products)
   - Special export format with product columns
   - Per-product status and remarks

3. **HUMANITAIRE** (campagne_id=3):
   - Separate database (Data/humanitaire.db)
   - Complex metrics calculations
   - Prime calculation system
   - Team/TV assignments

---

## Important Notes for AI Assistants

### Security Considerations

1. **Never commit sensitive data**:
   - .env file is gitignored
   - *.db files are gitignored
   - Check .gitignore before adding files

2. **SQL Injection Prevention**:
   - Always use parameterized queries
   - Never interpolate user input into SQL strings
   - Use `?` placeholders

3. **CSRF Protection**:
   - Flask-WTF provides automatic CSRF protection
   - Include CSRF tokens in forms
   - AJAX requests must include `X-CSRFToken` header

4. **Password Security**:
   - Always use bcrypt for hashing
   - Never log or display passwords
   - Use `bcrypt.checkpw()` for verification

### Performance Considerations

1. **Database Indexes**: Critical indexes already defined in db_schema.py
2. **Large Datasets**: Use pagination in queries (LIMIT/OFFSET)
3. **Excel Export**: Use streaming for large files
4. **WebSocket Updates**: Throttle frequent updates

### Testing Requirements

1. **Write tests for new features**: Follow pytest patterns in tests/
2. **Use conftest.py**: Centralized test configuration
3. **Database fixtures**: Create test databases in temporary locations
4. **Clean up**: Tests should not affect production databases

### File Organization

1. **Root-level scripts**: One-off utilities or migrations
2. **Core modules**: app.py, db_schema.py, humanitaire.py, etc.
3. **Templates**: One file per page/view
4. **Static files**: CSS, images, uploads
5. **Data directory**: Never commit, always gitignore

### Git Workflow

1. **Branch naming**: Follow convention `claude/claude-md-{session-id}`
2. **Commit messages**: Clear, descriptive, imperative mood
3. **Before committing**:
   - Run tests: `pytest`
   - Check for sensitive data
   - Verify .gitignore coverage
4. **Push carefully**: Use `git push -u origin <branch-name>`

### When Modifying Existing Code

1. **Read the entire function/file first**
2. **Understand the context**: Why does it exist? What does it do?
3. **Check for dependencies**: Where is this code called from?
4. **Preserve existing behavior**: Don't break existing functionality
5. **Match existing style**: Consistency is key
6. **Test thoroughly**: Ensure all edge cases work

### Common Pitfalls to Avoid

1. **Don't create duplicate schema definitions**: Use db_schema.py
2. **Don't hardcode database paths**: Use configuration or parameters
3. **Don't skip CSRF protection**: Always include tokens
4. **Don't ignore encoding issues**: Use UTF-8, handle accents properly
5. **Don't forget error handling**: Wrap database operations in try/except
6. **Don't skip input validation**: Validate all user input
7. **Don't commit database files**: Check .gitignore
8. **Don't bypass authentication**: Always check session
9. **Don't use ORM**: This project uses raw SQL intentionally
10. **Don't add unnecessary dependencies**: Keep requirements minimal

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Aircall API Configuration
AIRCALL_API_ID=your_aircall_api_id
AIRCALL_API_TOKEN=your_aircall_api_token

# Optional: Database paths (defaults used if not specified)
# CRM_DB_PATH=crm.db
# HUMA_DB_PATH=Data/humanitaire.db

# Optional: Currency conversion rate
# TAUX_EUR_VERS_DT=3.30
```

---

## Additional Resources

### Key Files for Understanding the System

1. **Start here**: `db_schema.py` - Understand the data model
2. **Then read**: `app.py` (first 200 lines) - Understand configuration and imports
3. **Core logic**: `humanitaire.py` - Understand business rules
4. **Data pipeline**: `etl_incremental.py` - Understand data ingestion

### API Documentation

**Aircall API** (used for call tracking):
- Endpoint format: `https://api.aircall.io/v1/{resource}`
- Authentication: HTTP Basic Auth (API_ID:API_TOKEN)
- Used for: Number searches, call tagging

### Database Schema Visualization

```
campagnes (1:N) agents (N:M) clients (1:N) historique_clients
                  |                  |
                  +-- journal_connexions

Separate DB:
humanitaire.db
  ├── calls (call records)
  ├── grh_hours (work hours)
  └── objectifs (agent objectives)

CRM.db also includes:
  └── primes_huma (prime calculation matrix)
```

---

## Quick Reference

### Database Paths
- Main CRM: `crm.db` (or configured path)
- Humanitarian: `Data/humanitaire.db`
- Data files: `Data/inbox/` → `Data/archive/`

### Key Commands
```bash
# Run application
python app.py

# Run tests
pytest

# Create admin
python set_admin.py

# Import data
python etl_incremental.py --db Data/humanitaire.db --appels Data/inbox

# Build CSS
npx tailwindcss -i ./static/css/tailwind.css -o ./static/css/output.css --watch
```

### Important Code Locations
- Routes: `app.py` (decorated with `@app.route`)
- Schema: `db_schema.py` (all schema functions)
- Templates: `templates/*.html`
- Business logic: `humanitaire.py`, `currency_utils.py`
- ETL: `etl_incremental.py`, `etl_import.py`

---

## Version History

- **2025-11-30**: Initial CLAUDE.md creation
  - Consolidated schema management in db_schema.py
  - All tests passing (10 tests)
  - Multi-campaign support fully functional

---

## Contact & Support

For questions about this codebase or to report issues:
1. Check this CLAUDE.md file first
2. Review relevant module documentation in docstrings
3. Check git history for context: `git log --follow <file>`
4. Run tests to verify expected behavior: `pytest -v`

---

*This document is maintained for AI assistants working on the CRM-Web-V2 codebase. Keep it updated when making significant architectural changes.*
