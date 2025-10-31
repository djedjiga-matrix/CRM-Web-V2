# etl_import.py
import os, re, sqlite3, pandas as pd

DATA_DIR = "Data"
DB = "humanitaire.db"

def to_float_eur(x):
    if pd.isna(x): return 0.0
    s = str(x).strip()
    s = re.sub(r"[^\d,.\-]", "", s).replace(",", ".")
    try: return float(s)
    except: return 0.0

def read_calls(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(path, sep=";", quotechar='"', dtype=str, encoding="utf-8",
                         engine="python", on_bad_lines="skip")
    else:
        df = pd.read_excel(path)
    df.columns = [c.strip() for c in df.columns]

    # petit helper pour retrouver des colonnes malgré les variations
    def pick(*cands):
        lower = {c.lower(): c for c in df.columns}
        for c in cands:
            if c.lower() in lower: return lower[c.lower()]
        return None

    c_agent  = pick("AGENT","Agent","TV")
    c_stat   = pick("LIB_STATUS","Status","STATUT","Lib_Status","LIB_STATUT")
    c_det    = pick("LIB_DETAIL","Lib_Detail","Detail","Détail","LIB DETAIL","Détail appel")
    c_mont   = pick("Don","Montant_Don","Montant don","Montant","Montant (€)","MONTANT_DON")
    c_date   = pick("DATE","Date","DATE_APPEL","DATE APPEL","CallDate")

    if not (c_agent and c_stat and c_mont):
        return pd.DataFrame()

    base_code = os.path.basename(path)[:3].upper()

    out = pd.DataFrame({
        "base_code": base_code,
        "agent": df[c_agent].astype(str).fillna("").str.strip(),
        "status": df[c_stat].astype(str).fillna(""),
        "detail": df[c_det].astype(str).fillna("") if c_det else "",
        "montant": df[c_mont].map(to_float_eur)
    })
    # date
    if c_date:
        s = pd.to_datetime(df[c_date], errors="coerce", dayfirst=True)
    else:
        s = pd.NaT
    out["call_date"] = s.dt.date

    # flags utiles pour les KPI
    def norm(s):
        return (s.astype(str)
                 .str.normalize("NFKD")
                 .str.encode("ascii","ignore")
                 .str.decode("ascii")
                 .str.lower())
    text = norm(out["status"]) + " " + norm(out["detail"])

    is_donmail = text.str.contains(r"\bdon\s*en\s*ligne\b|\bdon\s*par\s*email\b|\blien\s*(de|pour)\s*don\b")
    is_don     = (text.str.contains(r"\bdam\b|\bdon\s*(avec|avc)?\s*montant\b|\biban\b|\bsepa\b|\bprelevement\b|\bvalidation\s*iban\b")
                  & (~is_donmail))
    is_indecis = text.str.contains(r"\bind[ée]cis\b.*\bdon\b")
    is_refus   = text.str.contains(r"\brefus\b")

    out["is_donmail"] = is_donmail.astype(int)
    out["is_don"]     = is_don.astype(int)
    out["is_indecis"] = is_indecis.astype(int)
    out["is_refus"]   = is_refus.astype(int)
    out["is_cu"]      = (out["is_don"] | out["is_donmail"] | out["is_indecis"] | out["is_refus"]).astype(int)
    return out

def read_grh_hours(xlsx_path):
    try:
        xls = pd.ExcelFile(xlsx_path)
    except:
        return pd.DataFrame()
    parts=[]
    for sh in xls.sheet_names:
        dt = pd.to_datetime(sh, format="%Y-%m-%d", errors="coerce")
        if pd.isna(dt): 
            continue
        try:
            df = pd.read_excel(xlsx_path, sheet_name=sh, header=6)
        except:
            continue
        lower={c.lower():c for c in df.columns}
        tv   = lower.get("agents") or lower.get("agent") or lower.get("tv")
        dur  = (lower.get("heure prod") or lower.get("heur prod") or
                lower.get("durée production") or lower.get("duree production") or
                lower.get("heures") or lower.get("production"))
        if not (tv and dur): 
            continue
        tmp = df[[tv,dur]].copy()
        tmp.columns = ["agent","duration"]
        def to_hours(v):
            if pd.isna(v): return 0.0
            s=str(v).strip().replace(",",".")
            if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", s):
                try: 
                    return pd.to_timedelta(s).total_seconds()/3600.0
                except: 
                    return 0.0
            try: return float(s)
            except: return 0.0
        tmp["heures"] = tmp["duration"].map(to_hours)
        tmp["jour"] = dt.date()
        tmp["agent"] = tmp["agent"].astype(str).str.strip().replace({"":"INCONNU"})
        tmp = tmp[tmp["agent"]!="INCONNU"]
        parts.append(tmp[["jour","agent","heures"]])
    if not parts: 
        return pd.DataFrame()
    return pd.concat(parts).groupby(["jour","agent"], as_index=False)["heures"].sum()

def read_objectifs(path):
    df = pd.read_excel(path)
    lower={c.lower():c for c in df.columns}
    tv   = lower.get("agent") or lower.get("tv") or lower.get("agents")
    o    = lower.get("objectif_dons")
    dm   = lower.get("don_moyen_cible")
    p    = lower.get("prime_base_eur")
    if not (tv and o and dm and p):
        return pd.DataFrame()
    out = df[[tv,o,dm,p]].copy()
    out.columns = ["agent","OBJECTIF_DONS","DON_MOYEN_CIBLE","PRIME_BASE_EUR"]
    out["agent"] = out["agent"].astype(str).str.strip()
    for k in ["OBJECTIF_DONS","DON_MOYEN_CIBLE","PRIME_BASE_EUR"]:
        out[k] = pd.to_numeric(out[k], errors="coerce").fillna(0.0)
    return out

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS calls(
        id INTEGER PRIMARY KEY,
        base_code TEXT,
        agent TEXT,
        status TEXT,
        detail TEXT,
        montant REAL,
        call_date DATE,
        is_donmail INTEGER,
        is_don INTEGER,
        is_indecis INTEGER,
        is_refus INTEGER,
        is_cu INTEGER
    );
    CREATE TABLE IF NOT EXISTS grh_hours(
        jour DATE,
        agent TEXT,
        heures REAL,
        PRIMARY KEY(jour, agent)
    );
    CREATE TABLE IF NOT EXISTS objectifs(
        agent TEXT PRIMARY KEY,
        OBJECTIF_DONS REAL,
        DON_MOYEN_CIBLE REAL,
        PRIME_BASE_EUR REAL
    );
    CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(call_date);
    CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent);
    CREATE INDEX IF NOT EXISTS idx_calls_base ON calls(base_code);
    """)
    con.commit()

    # Ingestion CALLS
    files = [os.path.join(DATA_DIR,f) for f in os.listdir(DATA_DIR)]
    to_upsert=[]
    for f in files:
        low=f.lower()
        if any(k in low for k in ["extract_grh","grh"]): 
            continue
        if os.path.splitext(f)[1].lower() not in [".csv",".xlsx",".xls"]:
            continue
        df = read_calls(f)
        if not df.empty:
            to_upsert.append(df)
    if to_upsert:
        all_calls = pd.concat(to_upsert, ignore_index=True)
        all_calls.to_sql("calls", con, if_exists="append", index=False)

    # Ingestion GRH
    for f in files:
        if "extract_grh" in f.lower():
            g = read_grh_hours(f)
            if not g.empty:
                g.to_sql("grh_hours", con, if_exists="append", index=False)

    # Ingestion OBJECTIFS/PRIMES
    obj_path = os.path.join(DATA_DIR, "Prime don.xlsx")
    if os.path.exists(obj_path):
        obj = read_objectifs(obj_path)
        if not obj.empty:
            cur.execute("DELETE FROM objectifs")
            con.commit()
            obj.to_sql("objectifs", con, if_exists="append", index=False)

    con.commit()
    con.close()
    print("✅ Import terminé. Base créée:", DB)

if __name__ == "__main__":
    main()
