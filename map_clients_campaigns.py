# map_clients_campaigns.py
import sqlite3, os

DB = "crm_clients.db"

def table_cols(cur, table):
    info = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    cols = [c[1] for c in info]
    return cols

def exists(cols, name):
    return name in cols

def safe_count(cur, table):
    try:
        return cur.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
    except Exception:
        return None

def group_by_indicator(cur, table, cols):
    """
    Essaie dans l'ordre :
    - campagne_id + JOIN campagnes
    - sinon colonne 'campagne'
    - sinon 'projet'
    - sinon 'source'
    Retourne: (label, rows) où rows = [(val, n), ...]
    """
    if exists(cols, "campagne_id"):
        q = f"""
        SELECT COALESCE(c.nom,'(sans nom)') AS campagne, COUNT(*)
        FROM {table} t
        LEFT JOIN campagnes c ON t.campagne_id = c.id
        GROUP BY campagne
        ORDER BY COUNT(*) DESC
        """
        lab = "campagne (via campagne_id)"
        rows = cur.execute(q).fetchall()
        return lab, rows

    for cand in ["campagne", "projet", "source"]:
        if exists(cols, cand):
            q = f"""
            SELECT COALESCE({cand},'(vide)') AS val, COUNT(*)
            FROM {table}
            GROUP BY val
            ORDER BY COUNT(*) DESC
            """
            lab = f"{cand}"
            rows = cur.execute(q).fetchall()
            return lab, rows

    return "(aucun indicateur trouvé)", []

def find_samples(cur, table, cols, keywords=("SFR","VALANDRE")):
    # essaie de trouver 5 exemples par keyword
    out = {}
    text_cols = [c for c in cols if c not in ("id","campagne_id")]
    for kw in keywords:
        found = []
        for col in text_cols:
            try:
                q = f"SELECT * FROM '{table}' WHERE LOWER(COALESCE({col},'')) LIKE ? LIMIT 5"
                rows = cur.execute(q, (f"%{kw.lower()}%",)).fetchall()
                if rows:
                    # prendre les noms de colonnes pour dict lisible
                    out_cols = [d[0] for d in cur.execute(f"PRAGMA table_info('{table}')").fetchall()]
                    for r in rows:
                        found.append(dict(zip(out_cols, r)))
            except Exception:
                pass
        out[kw] = found[:5]
    return out

def main():
    if not os.path.exists(DB):
        print(f"❌ {DB} introuvable.")
        return
    con = sqlite3.connect(DB)
    cur = con.cursor()

    for table in ["clients", "clients_valandre"]:
        print(f"\n==== TABLE: {table} ====")
        n = safe_count(cur, table)
        if n is None:
            print("  (table absente)")
            continue
        print(f"  Lignes: {n}")
        cols = table_cols(cur, table)
        print(f"  Colonnes: {', '.join(cols)}")

        label, rows = group_by_indicator(cur, table, cols)
        print(f"  Répartition par {label}:")
        if rows:
            for val, cnt in rows[:20]:
                print(f"    - {val}: {cnt}")
        else:
            print("    (aucune donnée regroupable)")

        # Exemples ciblés
        samples = find_samples(cur, table, cols)
        for kw, lst in samples.items():
            print(f"  Exemples contenant '{kw}': {len(lst)} trouvé(s)")
            for x in lst:
                # n'afficher que quelques champs utiles si présents
                keys_order = [k for k in ["id","nom","prenom","telephone","email","offre","campagne","projet","source","base","campagne_id"] if k in x.keys()]
                if not keys_order:
                    keys_order = list(x.keys())[:6]
                mini = {k: x.get(k) for k in keys_order}
                print("    ", mini)

    con.close()

if __name__ == "__main__":
    main()
