import sqlite3

DB_NAME = "crm_clients.db"
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()
try:
    c.execute("ALTER TABLE clients ADD COLUMN DEUXIEME_ADRESSE TEXT DEFAULT ''")
    print("Colonne DEUXIEME_ADRESSE ajoutée.")
except Exception as e:
    print("Déjà ajoutée ou erreur :", e)
conn.commit()
conn.close()
