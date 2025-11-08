# dedupe_merge_apply.py
import sqlite3, os
from datetime import datetime

DB = "crm_clients.db"
assert os.path.exists(DB), f"{DB} introuvable"

ADRS_FIELDS = ["DEUXIEME_ADRESSE", "TROISIEME_ADRESSE"]
SAFE_FILL_FIELDS = ["NOM_CLIENT", "PRENOM_CLIENT", "STATUT", "AGENT", "DATE_SIGNATURE", "DATE_MODIF"]

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
    # Priorité à la fiche la plus "fraîche"
    ds = parse_date(row.get("DATE_SIGNATURE"))
    dm = parse_date(row.get("DATE_MODIF"))
    return (ds or datetime.min, dm or datetime.min, row.get("id", 0))

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

# Normaliser les téléphones (trim) pour éviter les faux doublons
cur.execute("UPDATE clients SET TELEPHONE = TRIM(TELEPHONE) WHERE TELEPHONE IS NOT NULL")

# Lister les groupes en doublon (par campagne + téléphone)
dupes = cur.execute("""
SELECT c.campagne_id, TRIM(COALESCE(c.TELEPHONE,'')) AS tel, COUNT(*) AS n
FROM clients c
GROUP BY c.campagne_id, TRIM(COALESCE(c.TELEPHONE,''))
HAVING tel <> '' AND n > 1
ORDER BY n DESC, tel
""").fetchall()

if not dupes:
    print("✅ Aucun doublon à traiter. Rien à faire.")
    con.close()
    raise SystemExit()

print(f"⚠️ {len(dupes)} groupe(s) de doublons à fusionner\n")
total_deleted = 0
groups_done = 0


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

    keep = max(dicts, key=score)
    delete = [d for d in dicts if d["id"] != keep["id"]]

    # Fusionner toutes les adresses non vides
    pool = []
    for d in dicts:
        for f in ADRS_FIELDS:
            pool.append(d.get(f))
    uniq = unique_nonempty(pool)

    new_deux = keep.get("DEUXIEME_ADRESSE")
    new_trois = keep.get("TROISIEME_ADRESSE")

    if len(uniq) >= 1:
        new_deux = uniq[0]
    if len(uniq) >= 2:
        # si plusieurs valeurs, on met tout le reste dans TROISIEME_ADRESSE, séparé par " | "
        new_trois = " | ".join(uniq[1:])
    # Si aucune adresse trouvée, on garde les valeurs existantes de la fiche conservée

    # Remplir doucement certains champs manquants sur la fiche conservée
    updates = {"DEUXIEME_ADRESSE": new_deux, "TROISIEME_ADRESSE": new_trois}
    for f in SAFE_FILL_FIELDS:
        if not norm(keep.get(f)):
            for d in dicts:
                v = norm(d.get(f))
                if v:
                    updates[f] = v
                    break

    # Préparer UPDATE dynamique
    set_parts = []
    params = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        params.append(v)
    # Date_modif à aujourd'hui si le champ existe
    if "DATE_MODIF" in cols:
        set_parts.append("DATE_MODIF = DATE('now')")

    params.append(keep["id"])
    cur.execute(f"UPDATE clients SET {', '.join(set_parts)} WHERE id = ?", params)

    # Supprimer les autres enregistrements du groupe
    for d in delete:
        cur.execute("DELETE FROM clients WHERE id = ?", (d["id"],))
        total_deleted += 1

    groups_done += 1
    print(f"✔️  Tél {tel} | Campagne {keep.get('campagne_nom')} : gardé id={keep['id']}, supprimé {len(delete)} doublon(s)")

con.commit()
con.close()
print(f"\n✅ Fusion terminée : {groups_done} groupe(s) traité(s), {total_deleted} ligne(s) supprimée(s).")
