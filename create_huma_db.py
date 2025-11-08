import sqlite3

db = "humanitaire.db"
con = sqlite3.connect(db)
cur = con.cursor()

# Table des appels
cur.execute("""
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT,
    agent TEXT,
    call_date TEXT,
    is_cu INTEGER DEFAULT 0,
    is_don INTEGER DEFAULT 0,
    is_donmail INTEGER DEFAULT 0,
    is_indecis INTEGER DEFAULT 0,
    montant REAL DEFAULT 0.0
)
""")

# Table GRH
cur.execute("""
CREATE TABLE IF NOT EXISTS grh_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jour TEXT,
    agent TEXT,
    heures REAL DEFAULT 0.0
)
""")

# Table des primes
cur.execute("""
CREATE TABLE IF NOT EXISTS primes_huma (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dons_cible INTEGER NOT NULL,
    don_moyen_cible INTEGER NOT NULL,
    prime_eur REAL NOT NULL,
    prime_dt REAL NOT NULL
)
""")

# Table log imports
cur.execute("""
CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fichier TEXT,
    lignes_importees INTEGER,
    date_import TEXT
)
""")

con.commit()
con.close()
print("✅ Base 'humanitaire.db' créée avec succès.")
