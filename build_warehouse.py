# build_warehouse.py
# Entrepôt minimal pour CRM Humanitaire :
# - raw_calls (CSV)  | raw_grh (XLSX) | primes_huma | kpi_daily_agent (agrégats)
# - Import robuste (toutes lignes conservées) + agrégation KPI par jour/agent/base

import os, re, glob, sqlite3, hashlib, json
from datetime import datetime
import pandas as pd

# ---------- PARAMS (adapte si besoin) ----------
DB_PATH = "humanitaire.db"
CALLS_GLOB = r"C:\Users\HP\Mes_Projet\Crm_web - V2\Data\*.csv"             # exports HIM/IMA...
GRH_GLOB   = r"C:\Users\HP\Mes_Projet\Crm_web - V2\Data\extract_grh*.xlsx" # heures prod

# ---------- Utils génériques ----------
def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _nt(x) -> str:
    return ("" if x is None else str(x)).strip()

def _norm_col(s: str) -> str:
    s = (s or "")
    s = s.replace("\ufeff","")
    s = s.strip().lower()
    repl = {"é":"e","è":"e","ê":"e","à":"a","î":"i","ï":"i","ô":"o","ö":"o","ç":"c"}
    for k,v in repl.items(): s = s.replace(k,v)
    s = re.sub(r"\s+", " ", s)
    return s

_NUM_RE = re.compile(r"[+-]?\d+(?:[.,]\d+)?")

def _to_float(x, default=0.0) -> float:
    if x is None or (isinstance(x,float) and pd.isna(x)): return default
    s = str(x).replace("\u00A0","").replace(" ","").strip()
    if not s: return default
    m = _NUM_RE.search(s)
    if not m: return default
    val = m.group(0).replace(",",".")
    try:
        return float(val)
    except Exception:
        return default

def to_iso(d):
    if d is None or (isinstance(d,float) and pd.isna(d)):
        return None
    try:
        return pd.to_datetime(d).date().isoformat()
    except Exception:
        for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%m/%d/%Y","%Y/%m/%d"):
            try:
                return datetime.strptime(str(d), fmt).date().isoformat()
            except Exception:
                continue
    return None

def _read_any_xl(path: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, engine="openpyxl")
    except Exception:
        return pd.read_excel(path)

def _read_any_csv(path: str) -> pd.DataFrame:
    for sep in [";","\,","\t","|", ","]:
        for enc in ["utf-8-sig","utf-8","latin1"]:
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str, keep_default_na=False, na_values=[""])
                if not df.empty:
                    return df
            except Exception:
                continue
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    except Exception:
        return pd.DataFrame()

def _date_from_filename(fname: str):
    base = os.path.basename(fname)
    m = re.search(r"(\d{8})", base)  # YYYYMMDD ou DDMMYYYY
    if not m: return None
    blk = m.group(1)
    # YYYYMMDD
    try:
        y, mo, d = int(blk[0:4]), int(blk[4:6]), int(blk[6:8])
        return datetime(y, mo, d).date().isoformat()
    except Exception:
        pass
    # DDMMYYYY
    try:
        d, mo, y = int(blk[0:2]), int(blk[2:4]), int(blk[4:8])
        return datetime(y, mo, d).date().isoformat()
    except Exception:
        return None

def _base_from_filename(fname: str):
    stem = os.path.splitext(os.path.basename(fname))[0]
    return stem.split("_")[0].upper()

def _looks_like_header(row_dict: dict) -> bool:
    vals = " ".join([_norm_col(v) for v in row_dict.values() if isinstance(v,str)])
    hints = ["agents","groupe d'agents","heure prod","duree conversation","pause","menu","h9 -10h","h10 -11h","debut","fin"]
    return any(h in vals for h in hints)

def _status_flags(s: str):
    s = _norm_col(s)
    is_donmail = int(any(x in s for x in ["don mail","don en ligne","email","lien de don","paylink","lien"]))
    is_don = 0
    if not is_donmail:
        is_don = int(any(x in s for x in ["iban","sepa","prelevement","prelevement sepa","dam","don avec montant","ok iban","ok sepa","coord banc"]))
    is_indecis = int(any(x in s for x in ["indecis","indecision","rappel","hesite","a rappeler","à rappeler"]))
    is_refus   = int(any(x in s for x in ["refus","ko","non","raccroche","pas interesse","pas intéressé"]))
    is_cu = int(bool(is_don or is_donmail or is_indecis or is_refus))
    return is_cu, is_don, is_donmail, is_indecis

# ---------- Schéma ----------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_date   TEXT,
    base        TEXT,
    agent       TEXT,
    statut      TEXT,
    montant     REAL,
    source_file TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    row_signature TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_calls_src ON raw_calls(source_file, source_row);

CREATE TABLE IF NOT EXISTS raw_grh (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jour        TEXT,
    agent       TEXT,
    heures      REAL,
    source_file TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    row_signature TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_grh_src ON raw_grh(source_file, source_row);

CREATE TABLE IF NOT EXISTS primes_huma (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dons_cible INTEGER NOT NULL,
    don_moyen_cible INTEGER NOT NULL,
    prime_eur REAL NOT NULL,
    prime_dt  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_primes ON primes_huma(dons_cible, don_moyen_cible);

-- Table d'agrégats (on la remplit via Python pour garder un calcul clair)
CREATE TABLE IF NOT EXISTS kpi_daily_agent (
    jour TEXT NOT NULL,
    agent TEXT NOT NULL,
    base TEXT,
    Cu INTEGER,
    Don INTEGER,
    Don_en_ligne INTEGER,
    Indecis INTEGER,
    Montant_Don REAL,
    Fich_T INTEGER,
    Heur_Prod REAL,
    Don_Moyen REAL,
    Tx_accord REAL,
    Cu_H REAL,
    Tx_Argu REAL,
    PRIMARY KEY (jour, agent, base)
);
"""

def ensure_schema(db_path: str):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.executescript(SCHEMA_SQL)
        con.commit()

# ---------- Import CSV -> raw_calls ----------
def import_calls(db_path: str, glob_pattern: str) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[CALLS] Aucun fichier pour {glob_pattern}")
        return 0

    total = 0
    for path in files:
        name = os.path.basename(path)
        if os.path.splitext(name)[1].lower() not in [".csv",".txt",".xlsx",".xls"]:
            continue

        # Autoriser aussi les XLSX ici si jamais une base d'appels est Excel
        if name.lower().endswith((".xlsx",".xls")):
            try:
                df = _read_any_xl(path)
            except Exception as e:
                print(f"[CALLS] Erreur lecture XLSX {name}: {e}")
                continue
        else:
            try:
                df = _read_any_csv(path)
            except Exception as e:
                print(f"[CALLS] Erreur lecture CSV {name}: {e}")
                continue

        if df.empty:
            print(f"[CALLS] (skip) vide: {name}")
            continue

        df.columns = [c.replace("\ufeff","") for c in df.columns]
        norm2real = {_norm_col(c): c for c in df.columns}

        # détection souple
        def pick(keys, default=None):
            for k in keys:
                if k in norm2real: return norm2real[k]
            return default

        key_agent   = pick(["agent","tv","televendeur","tele-vendeur","agents","agent login","commercial","operateur","operatrice","login","user","utilisateur"])
        key_date    = pick(["date","jour","call_date","date appel","date_appel","calldate","dateappel","date appel (yyyy-mm-dd)"])
        key_base    = pick(["base","campagne","campaign","operation","campagne libelle","campagne_libelle"])
        key_statut  = pick(["type","statut","status","lib_statut","lib status","resultat","lib resultat","result"])
        key_montant = pick(["montant","montant_don","don","amount","don_montant","montant don","don montant"])

        fb_date = _date_from_filename(name)
        fb_base = _base_from_filename(name)

        rows = []
        n_total = len(df)
        n_head = 0
        for idx, r in df.iterrows():
            raw = r.to_dict()
            if _looks_like_header(raw):
                n_head += 1
                continue

            agent  = _nt(r.get(key_agent)) if key_agent in df.columns else ""
            d      = to_iso(r.get(key_date)) if key_date in df.columns else None
            base   = _nt(r.get(key_base)).upper() if key_base in df.columns else ""

            if not d and fb_date: d = fb_date
            if not base and fb_base: base = fb_base

            statut  = _nt(r.get(key_statut)) if key_statut in df.columns else ""
            montant = _to_float(r.get(key_montant), 0.0) if key_montant in df.columns else 0.0

            # on garde même si agent vide : utile pour tracabilité par base/jour
            sig = _sha1(json.dumps({k:_nt(v) for k,v in raw.items()}, sort_keys=True, ensure_ascii=False))
            rows.append((d, base, agent, statut, montant, name, int(idx) if isinstance(idx,(int,float)) else 0, sig))

        if rows:
            with sqlite3.connect(db_path) as con:
                cur = con.cursor()
                cur.executemany("""
                    INSERT OR IGNORE INTO raw_calls
                    (call_date, base, agent, statut, montant, source_file, source_row, row_signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)
                con.commit()
                ins = cur.rowcount if cur.rowcount is not None else 0
                total += ins
        print(f"[CALLS] {name} | lignes={n_total} | insérées≈{ins} | entetes={n_head}")
    print(f"[CALLS] ✅ Total insérées≈{total}")
    return total

# ---------- Import XLSX GRH -> raw_grh ----------
def import_grh(db_path: str, glob_pattern: str) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[GRH] Aucun fichier pour {glob_pattern}")
        return 0

    total = 0
    for path in files:
        name = os.path.basename(path)
        try:
            df = _read_any_xl(path)
        except Exception as e:
            print(f"[GRH] Erreur lecture {name}: {e}")
            continue
        if df.empty:
            print(f"[GRH] (skip) vide: {name}")
            continue
        df.columns = [c.replace("\ufeff","") for c in df.columns]
        norm2real = {_norm_col(c): c for c in df.columns}

        key_date  = None
        for k in ["date","jour","day","date_jour"]:
            if k in norm2real: key_date = norm2real[k]; break
        key_agent = None
        for k in ["agent","tv","agents","login","user","utilisateur"]:
            if k in norm2real: key_agent = norm2real[k]; break
        key_heures = None
        for k in ["heures","heure prod","heur prod","h_prod","heure production","heures production"]:
            if k in norm2real: key_heures = norm2real[k]; break

        rows = []
        n_total = len(df)
        n_head = 0
        for idx, r in df.iterrows():
            raw = r.to_dict()
            if _looks_like_header(raw):
                n_head += 1
                continue

            jour  = to_iso(r.get(key_date)) if key_date in df.columns else None
            agent = _nt(r.get(key_agent)) if key_agent in df.columns else ""
            h     = _to_float(r.get(key_heures), 0.0) if key_heures in df.columns else 0.0
            if not jour and _date_from_filename(name):
                jour = _date_from_filename(name)

            sig = _sha1(json.dumps({k:_nt(v) for k,v in raw.items()}, sort_keys=True, ensure_ascii=False))
            rows.append((jour, agent, h, name, int(idx) if isinstance(idx,(int,float)) else 0, sig))

        if rows:
            with sqlite3.connect(db_path) as con:
                cur = con.cursor()
                cur.executemany("""
                    INSERT OR IGNORE INTO raw_grh
                    (jour, agent, heures, source_file, source_row, row_signature)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, rows)
                con.commit()
                ins = cur.rowcount if cur.rowcount is not None else 0
                total += ins
        print(f"[GRH] {name} | lignes={n_total} | insérées≈{ins} | entetes={n_head}")
    print(f"[GRH] ✅ Total insérées≈{total}")
    return total

# ---------- Build KPI (écrase et reconstruit) ----------
def rebuild_kpi_table(db_path: str):
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("DELETE FROM kpi_daily_agent")  # on repart propre

        # Charger raw_calls en mémoire (colonnes utiles)
        calls = pd.read_sql("""
            SELECT call_date, base, agent, statut, montant
            FROM raw_calls
        """, con)

        if calls.empty:
            con.commit()
            print("[KPI] Aucun call en RAW, table KPI vide.")
            return

        # Flags à partir du statut libre
        tmp = []
        for _, r in calls.iterrows():
            is_cu, is_don, is_donmail, is_indecis = _status_flags(r["statut"] or "")
            tmp.append((r["call_date"], r["base"], r["agent"], is_cu, is_don, is_donmail, is_indecis, float(r["montant"] or 0.0)))
        calls2 = pd.DataFrame(tmp, columns=["jour","base","agent","is_cu","is_don","is_donmail","is_indecis","montant"])

        # Agrégat par jour/agent/base
        grp = calls2.groupby(["jour","agent","base"], dropna=False).agg(
            Cu=("is_cu","sum"),
            Don=("is_don","sum"),
            Don_en_ligne=("is_donmail","sum"),
            Indecis=("is_indecis","sum"),
            Montant_Don=("montant","sum"),
            Fich_T=("is_cu","count")   # total lignes (ou COUNT(*))
        ).reset_index()

        # Joindre heures prod depuis raw_grh
        grh = pd.read_sql("""SELECT jour, agent, SUM(COALESCE(heures,0.0)) AS Heur_Prod
                             FROM raw_grh GROUP BY jour, agent""", con)
        if not grh.empty:
            grp = grp.merge(grh, on=["jour","agent"], how="left")
        else:
            grp["Heur_Prod"] = 0.0
        grp["Heur_Prod"] = grp["Heur_Prod"].fillna(0.0)

        # KPIs calculés
        dons_count = (grp["Don"].fillna(0) + grp["Don_en_ligne"].fillna(0))
        grp["Don_Moyen"] = grp["Montant_Don"].fillna(0.0) / dons_count.replace(0, pd.NA)
        grp["Don_Moyen"] = grp["Don_Moyen"].fillna(0.0)

        grp["Tx_accord"] = dons_count / grp["Cu"].replace(0, pd.NA)
        grp["Tx_accord"] = grp["Tx_accord"].fillna(0.0)

        grp["Cu_H"] = grp["Cu"] / grp["Heur_Prod"].replace(0, pd.NA)
        grp["Cu_H"] = grp["Cu_H"].fillna(0.0)

        grp["Tx_Argu"] = (grp["Don"] + grp["Don_en_ligne"] + grp["Indecis"]) / grp["Cu"].replace(0, pd.NA)
        grp["Tx_Argu"] = grp["Tx_Argu"].fillna(0.0)

        # Insérer dans kpi_daily_agent
        rows = grp.fillna({"base": ""}).to_records(index=False)
        cur.executemany("""
            INSERT OR REPLACE INTO kpi_daily_agent
            (jour, agent, base, Cu, Don, Don_en_ligne, Indecis, Montant_Don, Fich_T, Heur_Prod, Don_Moyen, Tx_accord, Cu_H, Tx_Argu)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, list(rows))
        con.commit()
        print(f"[KPI] lignes agrégées: {len(grp)}")

# ---------- Main ----------
if __name__ == "__main__":
    ensure_schema(DB_PATH)

    # (Optionnel) vider les tables si tu veux repartir à zéro :
    # with sqlite3.connect(DB_PATH) as con:
    #     con.execute("DELETE FROM raw_calls")
    #     con.execute("DELETE FROM raw_grh")
    #     con.execute("DELETE FROM kpi_daily_agent")
    #     con.commit()

    import_calls(DB_PATH, CALLS_GLOB)
    import_grh(DB_PATH, GRH_GLOB)
    rebuild_kpi_table(DB_PATH)

    print("\n[DONE] Entrepôt et KPI reconstruits.")
