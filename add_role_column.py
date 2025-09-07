import sqlite3

DB_NAME = "crm_clients.db"

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

try:
    c.execute("ALTER TABLE agents ADD COLUMN ROLE TEXT NOT NULL DEFAULT 'agent';")
    print("Colonne ROLE ajoutée avec succès.")
except Exception as e:
    print("Erreur ou colonne déjà existante :", e)

conn.commit()
conn.close()
