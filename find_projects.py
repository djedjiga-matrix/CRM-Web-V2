# find_projects.py
import sqlite3, os

DB = "crm_clients.db"
TOKENS = ["SFR", "VALANDRE", "VALANDRÉ"]

def get_tables(cur):
    q = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    return [r[0] for r in cur.execute(q).fetchall()]

def get_text_columns(cur, table):
    q = f"PRAGMA table_info('{table}')"
    cols = cur.execute(q).fetchall()
    text_cols = [c[1] for c in cols if (c[2] or "").upper().startswith("TEXT") or c[2] == ""]
    return text_cols, [c[1] for c in cols]

def find_token(cur, table, token, text_cols):
    total = 0
    found_cols = []
    for col in text_cols:
        q = f"SELECT COUNT(*) FROM '{table}' WHERE LOWER(COALESCE({col},'')) LIKE ?"
        n = cur.execute(q, (f"%{token.lower()}%",)).fetchone()[0]
        if n:
            total += n
            found_cols.append((col, n))
    samples = []
    if total:
        _, all_cols = get_text_columns(cur, table)
        show_cols = [c for c in ["id","nom","prenom","telephone","email","offre","campagne","source","projet","base"] if c in all_cols]
        if not show_cols:
            show_cols = all_cols[:6]
        q = f"SELECT {', '.join(show_cols)} FROM '{table}' WHERE "
        q += " OR ".join([f"LOWER(COALESCE({col},'')) LIKE ?" for col,_ in found_cols])
        params = tuple([f"%{token.lower()}%"]*len(found_cols))
        samples = cur.execute(q+" LIMIT 5", params).fetchall()
        samples = [dict(zip(show_cols, row)) for row in samples]
    return total, found_cols, samples

def main():
    if not os.path.exists(DB):
        print(f"❌ {DB} introuvable.")
        return
    con = sqlite3.connect(DB)
    cur = con.cursor()
    print(f"🔎 Base : {DB}\n")
    tables = get_tables(cur)
    if not tables:
        print("Aucune table trouvée.")
        return
    print("Tables:", ", ".join(tables))
    print("\n--- Recherche des projets ---")
    for token in TOKENS:
        print(f"\n▶ Token: {token}")
        any_hit = False
        for t in tables:
            text_cols, _ = get_text_columns(cur, t)
            if not text_cols:
                continue
            total, cols, samples = find_token(cur, t, token, text_cols)
            if total:
                any_hit = True
                print(f"  • Table '{t}': {total} enregistrements")
                for c,n in cols:
                    print(f"     - Colonne {c}: {n}")
                if samples:
                    print("     Extraits:")
                    for s in samples:
                        print("       ", s)
        if not any_hit:
            print("  (aucune occurrence)")
    con.close()

if __name__ == "__main__":
    main()
