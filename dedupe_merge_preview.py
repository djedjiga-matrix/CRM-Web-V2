# dedupe_merge_preview.py
import sqlite3, os
from datetime import datetime

DB = "crm_clients.db"
assert os.path.exists(DB), f"{DB} introuvable"

ADRS_FIELDS = ["DEUXIEME_ADRESSE", "TROISIEME_ADRESSE"]
SAFE_FILL_FIELDS = ["NOM_CLIENT","PRENOM_CLIENT","STATUT","AGENT","DATE_SIGNATURE","DATE_MODIF"]

def norm(s):
    return (s or "").strip()

def parse_date(s):
    s = norm(s)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def score(row):
    ds = parse_date(row.get("DATE_SIGNATURE"))
    dm = parse_date(row.get("DATE_MODIF"))
    return (ds or datetime.min, dm or datetime.min, row.get("id",0))

def unique_nonempty(values):
    seen, out = set(), []
    for v in values:
        v = norm(v)
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out

con = sqlite3.connect(DB)
cur = con.cursor()

dupes = cur.execute("""
SELECT c.campagne_id, TRIM(COALESCE(c.TELEPHONE,'')) AS tel, COUNT(*) AS n
FROM clients c
GROUP BY c.campagne_id, TRIM(COALESCE(c.TELEPHONE,''))
HAVING tel <> '' AND n > 1
ORDER BY n DESC, tel
""").fetchall()

if not dupes:
    print("✅ Aucun doublon à fusionner (tu pourras poser directement l'index unique).")
    con.close()
    raise SystemExit()

print(f"⚠️ {len(dupes)} groupe(s) de doublons à traiter\n")

for campagne_id, tel, _ in dupes:
    rows = cur.execute("""
        SELECT c.*, k.nom AS campagne_nom
        FROM clients c
        LEFT JOIN campagnes k ON k.id = c.campagne_id
        WHERE c.campagne_id = ? AND TRIM(COALESCE(c.TELEPHONE,'')) = ?
        ORDER BY c.id
    """, (campagne_id, tel)).fetchall()
    cols = [d[0] for d in cur.description]
    dicts = [dict(zip(cols, r)) for r in rows]

    # Choix de la fiche "maîtresse" (plus récente, sinon id plus grand)
    keep = max(dicts, key=score)
    deletes = [d for d in dicts if d["id"] != keep["id"]]

    # Fusion des adresses (on collecte toutes les adresses non vides)
    pool = []
    for d in dicts:
        for f in ADRS_FIELDS:
            pool.append(d.get(f))
    uniq = unique_nonempty(pool)

    # Projection dans 2 champs (si >2, on concatène le reste dans TROISIEME_ADRESSE)
    new_deux = uniq[0] if len(uniq) >= 1 else norm(keep.get("DEUXIEME_ADRESSE"))
    if len(uniq) == 1:
        new_trois = norm(keep.get("TROISIEME_ADRESSE"))
    elif len(uniq) >= 2:
        # si plusieurs valeurs, on met la 2e en TROISIEME_ADRESSE + reste concaténé
        rest = uniq[1:]
        new_trois = " | ".join(rest)
    else:
        new_trois = norm(keep.get("TROISIEME_ADRESSE"))

    # Remplissage de quelques champs si vides (sans écraser des valeurs déjà présentes)
    preview_fill = {}
    for f in SAFE_FILL_FIELDS:
        if not norm(keep.get(f)):
            for d in dicts:
                v = norm(d.get(f))
                if v:
                    preview_fill[f] = v
                    break

    print(f"=== Campagne: {keep.get('campagne_nom')} | Tél: {tel} ===")
    print("→ Fiche conservée (id):", keep["id"])
    print("   Adresses actuelles:", {f: keep.get(f) for f in ADRS_FIELDS})
    print("   Adresses fusionnées:", {"DEUXIEME_ADRESSE": new_deux, "TROISIEME_ADRESSE": new_trois})
    if preview_fill:
        print("   Champs remplis (si vides):", preview_fill)
    print("→ Fiches supprimées:", [d["id"] for d in deletes])
    print()

con.close()
print("🛈 Si l'aperçu te convient, on appliquera la fusion puis la suppression.")
