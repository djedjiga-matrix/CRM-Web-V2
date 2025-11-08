# create_index_and_views.py
import sqlite3, os

DB = "crm_clients.db"
assert os.path.exists(DB), f"{DB} introuvable"

con = sqlite3.connect(DB)
cur = con.cursor()

# 1) Normaliser les téléphones (évite les faux doublons avec des espaces)
cur.execute("UPDATE clients SET TELEPHONE = TRIM(TELEPHONE) WHERE TELEPHONE IS NOT NULL")

# 2) Index UNIQUE par (campagne_id, TELEPHONE) quand le téléphone est non vide
cur.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_campagne_tel
ON clients(campagne_id, TELEPHONE)
WHERE TELEPHONE IS NOT NULL AND TRIM(TELEPHONE) <> '';
""")

# 3) Vues de confort
cur.execute("DROP VIEW IF EXISTS clients_sfr;")
cur.execute("""
CREATE VIEW clients_sfr AS
SELECT c.*
FROM clients c
WHERE c.campagne_id = (SELECT id FROM campagnes WHERE nom='EXOSPHERE_SFR' ORDER BY id LIMIT 1);
""")

cur.execute("DROP VIEW IF EXISTS clients_valandre_view;")
cur.execute("""
CREATE VIEW clients_valandre_view AS
SELECT c.*
FROM clients c
WHERE c.campagne_id = (SELECT id FROM campagnes WHERE nom='VALANDRE' ORDER BY id LIMIT 1);
""")

con.commit()
con.close()
print("✅ Index + vues créés avec succès.")
