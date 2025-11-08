# dedupe_preview.py
import sqlite3, os
from datetime import datetime

DB = "crm_clients.db"
assert os.path.exists(DB), f"{DB} introuvable"

def parse_date(s):
    if not s: return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

con = sqlite3.connect(DB)
cur = con.cursor()

# Récupérer les groupes de doublons par (campagne_id, téléphone normalisé)
dupes = cur.execute("""
SELECT c.campagne_id,
       TRIM(COALESCE(c.TELEPHONE,'')) AS tel,
       COUNT(*) AS n
FROM clients c
GROUP BY c.campagne_id, TRIM(COALESCE(c.TELEPHONE,''))
HAVING tel <> '' AND n > 1
ORDER BY n DESC, tel
""").fetchall()

if not dupes:
    print("✅ Aucun doublon. Tu peux passer direct à l'étape C (index + vues).")
    con.close()
    raise SystemExit()

print(f"⚠️ {len(dupes)} groupe(s) de doublons trouvé(s)\n")

all_deletes = []
for campagne_id, tel, n in dupes:
    # Charger les lignes du groupe
    rows = cur.execute("""
        SELECT c.*, k.nom AS campagne_nom
        FROM clients c
        LEFT JOIN campagnes k ON k.id = c.campagne_id
        WHERE c.campagne_id = ? AND TRIM(COALESCE(c.TELEPHONE,'')) = ?
        ORDER BY c.id
    """, (campagne_id, tel)).fetchall()

    cols = [d[0] for d in cur.description]
    def to_dict(row): return dict(zip(cols, row))

    dicts = [to_dict(r) for r in rows]

    # Règle proposée : garder la ligne la plus "fraîche"
    # 1) DATE_SIGNATURE la plus récente (si dispo)
    # 2) sinon DATE_MODIF la plus récente (si dispo)
    # 3) sinon id le plus élevé
    def score(d):
        ds = parse_date(d.get("DATE_SIGNATURE"))
        dm = parse_date(d.get("DATE_MODIF"))
        return (
            ds or datetime.min,
            dm or datetime.min,
            d.get("id", 0) or 0
        )

    keep = max(dicts, key=score)
    delete = [d for d in dicts if d["id"] != keep["id"]]

    print(f"=== Tél {tel} | Campagne_id={campagne_id} ({keep.get('campagne_nom')}) ===")
    print("→ On PROPOSE de GARDER :")
    print({k: keep.get(k) for k in ["id","NOM_CLIENT","PRENOM_CLIENT","DATE_SIGNATURE","DATE_MODIF","STATUT","AGENT","TELEPHONE"]})
    print("→ On PROPOSE de SUPPRIMER :")
    for d in delete:
        print({k: d.get(k) for k in ["id","NOM_CLIENT","PRENOM_CLIENT","DATE_SIGNATURE","DATE_MODIF","STATUT","AGENT","TELEPHONE"]})
    all_deletes += [d["id"] for d in delete]
    print()

if all_deletes:
    print("APERCU requêtes (à exécuter UNIQUEMENT si tu valides) :")
    for _id in all_deletes:
        print(f"DELETE FROM clients WHERE id={_id};")

con.close()
print("\n🛈 Si la proposition te va, on exécutera la suppression (étape B).")
