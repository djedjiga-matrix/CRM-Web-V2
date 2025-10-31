# import_huma_to_db.py
import os, re, argparse, sqlite3
import pandas as pd
from datetime import datetime
from humanitaire import (
    _resolve_many, _read_all_appels, _read_all_grh, _pick_col,
    _normalize_ascii_series, DEFAULT_APPELS_GLOB, DEFAULT_GRH_GLOB
)

def coerce_date(s):
    if pd.isna(s): return None
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        try:
            return datetime.strptime(str(s), "%d/%m/%Y").strftime("%Y-%m-%d")
        except Exception:
            return None

def import_calls(con, appels_pattern, date_debut=None, date_fin=None):
    df = _read_all_appels(_resolve_many(appels_pattern, DEFAULT_APPELS_GLOB),
                          date_debut, date_fin)
    if df is None or df.empty:
        print("[IMPORT] Aucun fichier d'appels lu.")
        return 0

    col_AGENT   = _pick_col(df, "AGENT", "Agent", "TV")
    col_STATUS  = _pick_col(df, "LIB_STATUS", "Status", "STATUT", "Lib_Status", "LIB_STATUT")
    col_DETAIL  = _pick_col(df, "LIB_DETAIL", "Lib_Detail", "DETAIL", "Détail")
    col_DON_EUR = _pick_col(df, "Don", "Montant_Don", "Montant don", "Montant", "Montant (€)", "MONTANT_DON")
    col_DATE    = _pick_col(df, "DATE") or _pick_col(df, "Date") \
                  or _pick_col(df, "DATE_APPEL","DATE APPEL","CallDate")

    if col_AGENT is None or col_STATUS is None or col_DON_EUR is None:
        raise RuntimeError("Colonnes requises manquantes dans les appels (AGENT/STATUS/MONTANT).")

    s_status = _normalize_ascii_series(df[col_STATUS]).str.lower()
    s_detail = _normalize_ascii_series(df[col_DETAIL]).str.lower() if col_DETAIL else pd.Series([""]*len(df))
    s_all = (s_status + " " + s_detail).str.strip()

    is_don_mail = (
        s_all.str.contains(r"\bdon\s*mail\b") |
        s_all.str.contains(r"\bdon\s*par\s*email\b") |
        s_all.str.contains(r"\bdon\s*en\s*ligne\b") |
        s_all.str.contains(r"\blien\s*(de|pour)?\s*don\b")
    )
    is_don = (
        s_all.str.contains(r"\bdam\b") |
        s_all.str.contains(r"\bdon\s*(avec|avc)?\s*montant\b") |
        s_all.str.contains(r"\biban\b|\bsepa\b|\bprelevement\b|\bvalidation\s*iban\b")
    ) & (~is_don_mail)
    is_indecis = s_all.str.contains(r"\bind[ée]cis\b")
    is_refus   = s_all.str.contains(r"\brefus\b") | s_all.str.contains(r"\bref\s*refus\b")
    is_cu      = is_don | is_don_mail | is_indecis | is_refus

    montant = (
        df[col_DON_EUR].astype(str)
        .str.replace(r"[^\d,.\-]", "", regex=True)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    montant = pd.to_numeric(montant, errors="coerce").fillna(0.0)

    agent = df[col_AGENT].astype(str).fillna("").str.strip()
    base  = df["__base_code__"].astype(str).fillna("").str.upper() if "__base_code__" in df.columns else ""
    call_date = df[col_DATE].apply(coerce_date) if col_DATE else pd.Series([None]*len(df))

    rows = []
    for a, b, d, cu, dn, dm, indec, m in zip(agent, base, call_date, is_cu, is_don, is_don_mail, is_indecis, montant):
        if not a:
            continue
        rows.append((d, b, a, int(cu), int(dn), int(dm), int(indec), float(m)))

    with con:
        con.execute("DELETE FROM calls")
        con.executemany("""
            INSERT INTO calls (call_date, base, agent, is_cu, is_don, is_donmail, is_indecis, montant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
    print(f"[IMPORT] calls : {len(rows)} lignes insérées.")
    return len(rows)

def import_grh(con, grh_pattern, date_debut=None, date_fin=None):
    df = _read_all_grh(_resolve_many(grh_pattern, DEFAULT_GRH_GLOB),
                       date_debut=date_debut, date_fin=date_fin)
    if df is None or df.empty:
        print("[IMPORT] Aucun GRH lu.")
        return 0

    col_tv  = _pick_col(df, "TV", "Agent", "Agents")
    col_dur = _pick_col(df, "Heur Prod", "Heures", "Production", "Duree production","Durée production")
    if col_tv is None or col_dur is None:
        print("[IMPORT] Colonnes GRH non trouvées.")
        return 0

    rows = []
    for a, h in zip(df[col_tv].astype(str).fillna("").str.strip(),
                    pd.to_numeric(df[col_dur], errors="coerce").fillna(0.0)):
        if not a:
            continue
        rows.append((None, a, float(h)))

    with con:
        con.execute("DELETE FROM grh_hours")
        con.executemany("""
            INSERT INTO grh_hours (jour, agent, heures) VALUES (?, ?, ?)
        """, rows)

    print(f"[IMPORT] grh_hours : {len(rows)} lignes insérées.")
    return len(rows)

def import_objectifs_from_excel(con, prime_excel_path):
    if not (prime_excel_path and os.path.exists(prime_excel_path)):
        print(f"[IMPORT] Fichier primes introuvable : {prime_excel_path}")
        return 0
    df = pd.read_excel(prime_excel_path)
    def norm(s):
        import unicodedata
        s = str(s or "")
        s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode("ascii")
        return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    df.rename(columns={c: norm(c) for c in df.columns}, inplace=True)
    col_tv = next((c for c in df.columns if c.lower() in ("tv","agent","agents","nom")), None)
    col_obj = next((c for c in df.columns if "objectif" in c.lower()), None)
    col_dm  = next((c for c in df.columns if "don_moyen" in c.lower()), None)
    col_pe  = next((c for c in df.columns if "prime" in c.lower()), None)
    if not col_tv:
        print("[IMPORT] Colonne TV/agent introuvable dans le fichier primes.")
        return 0
    rows = []
    for _, r in df.iterrows():
        tv = str(r.get(col_tv,"") or "").strip()
        if not tv:
            continue
        obj = pd.to_numeric(r.get(col_obj, 0), errors="coerce")
        dm  = pd.to_numeric(r.get(col_dm, 0), errors="coerce")
        pe  = pd.to_numeric(r.get(col_pe, 0), errors="coerce")
        rows.append((tv, int(obj if pd.notna(obj) else 0),
                        float(dm if pd.notna(dm) else 0.0),
                        float(pe if pd.notna(pe) else 0.0)))
    with con:
        con.execute("DELETE FROM objectifs")
        con.executemany("""
            INSERT INTO objectifs (agent, OBJECTIF_DONS, DON_MOYEN_CIBLE, PRIME_BASE_EUR)
            VALUES (?, ?, ?, ?)
        """, rows)
    print(f"[IMPORT] objectifs : {len(rows)} lignes insérées.")
    return len(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="crm_clients.db")
    ap.add_argument("--appels", default=r"C:\Users\HP\Mes_Projet\Crm_web - V2\Data\*.*")
    ap.add_argument("--grh",    default=r"C:\Users\HP\Mes_Projet\Crm_web - V2\Data\extract_grh*.xlsx")
    ap.add_argument("--primes", default=r"C:\Users\HP\Mes_Projet\Crm_web - V2\Data\Prime don.xlsx")
    ap.add_argument("--from", dest="date_debut", default=None)
    ap.add_argument("--to",   dest="date_fin",   default=None)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS calls      (id INTEGER PRIMARY KEY AUTOINCREMENT, call_date TEXT, base TEXT, agent TEXT, is_cu INTEGER, is_don INTEGER, is_donmail INTEGER, is_indecis INTEGER, montant REAL);
    CREATE TABLE IF NOT EXISTS grh_hours  (id INTEGER PRIMARY KEY AUTOINCREMENT, jour TEXT, agent TEXT, heures REAL);
    CREATE TABLE IF NOT EXISTS objectifs  (agent TEXT PRIMARY KEY, OBJECTIF_DONS INTEGER, DON_MOYEN_CIBLE REAL, PRIME_BASE_EUR REAL);
    """)

    import_calls(con, args.appels, args.date_debut, args.date_fin)
    import_grh(con, args.grh, args.date_debut, args.date_fin)
    import_objectifs_from_excel(con, args.primes)
    con.close()
    print("[IMPORT] Terminé.")

if __name__ == "__main__":
    main()
