# check_duplicates.py
import sqlite3, os, textwrap

DB = "crm_clients.db"
if not os.path.exists(DB):
    raise SystemExit(f"❌ {DB} introuvable")

con = sqlite3.connect(DB)
cur = con.cursor()

print("🔎 Vérification des doublons par (campagne_id, TELEPHONE) ...\n")

sql = """
SELECT 
  COALESCE(k.nom, '(campagne inconnue)') AS campagne,
  TRIM(COALESCE(c.TELEPHONE, '')) AS tel,
  COUNT(*) AS n,
  GROUP_CONCAT(c.id) AS ids
FROM clients c
LEFT JOIN campagnes k ON k.id = c.campagne_id
GROUP BY c.campagne_id, TRIM(COALESCE(c.TELEPHONE, ''))
HAVING tel <> '' AND n > 1
ORDER BY n DESC, campagne, tel
"""
rows = cur.execute(sql).fetchall()

if not rows:
    print("✅ Aucun doublon détecté. Tu peux créer l'index unique en toute sécurité.")
else:
    print(f"⚠️ {len(rows)} groupe(s) de doublons détecté(s). Détail ci-dessous :\n")
    for campagne, tel, n, ids in rows:
        print(f"- Campagne: {campagne:15s} | Tel: {tel:15s} | Occurrences: {n:2d} | ids={ids}")

con.close()
print("\n🛈 Rappel: l'index UNIQUE échouera tant qu'il reste au moins un groupe ci-dessus.")
