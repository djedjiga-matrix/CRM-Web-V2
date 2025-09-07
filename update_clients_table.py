import sqlite3

DB_NAME = "crm_clients.db"
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

try:
    c.execute("ALTER TABLE clients ADD COLUMN CREE_PAR TEXT")
except Exception as e:
    print("CREE_PAR déjà existant ou erreur :", e)

try:
    c.execute("ALTER TABLE clients ADD COLUMN MODIFIE_PAR TEXT")
except Exception as e:
    print("MODIFIE_PAR déjà existant ou erreur :", e)

try:
    c.execute("ALTER TABLE clients ADD COLUMN DATE_MODIF TEXT")
except Exception as e:
    print("DATE_MODIF déjà existant ou erreur :", e)

conn.commit()
conn.close()
print("Mise à jour terminée.")
