# Script pour créer un admin si inexistant
import sqlite3

DB_NAME = "crm_clients.db"
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

# Remplace par les infos de ton choix
login = "admin@moncrm.com"
mdp = "admin123"  # choisis un vrai mot de passe
nom = "admin"

# On regarde si l'admin existe déjà
c.execute("SELECT * FROM agents WHERE LOGIN=?", (login,))
if not c.fetchone():
    c.execute("INSERT INTO agents (NOM, LOGIN, MDP, ROLE, campagne_id) VALUES (?, ?, ?, ?, ?)", (nom, login, mdp, "admin", 1))
    print("Admin créé !")
else:
    print("L'admin existe déjà.")

conn.commit()
conn.close()
print("Terminé.")
