# etl_incremental.py — Import incrémental quotidien pour humanitaire/CRM (v3, avec auto-migration colonnes)
# - Création/migration schéma (tables + colonnes manquantes) + backfill row_key + dédup
# - Journal import (import_log) + index uniques
# - Lecture AUTO CSV/Excel, skip non tabulaires et GRH côté appels
# - Normalisation + détection robuste statuts (don/don_mail/indécis/refus)
# - Logs pour lignes ignorées et montants invalides

import os, glob, hashlib, sqlite3, argparse, re, unicodedata
from datetime import datetime
import pandas as pd

# ---------- Utils ----------
def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def norm_str(x) -> str:
    return ("" if x is None else str(x)).strip()

def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def normalize_text(s: str) -> str:
    return _strip_accents(s or "").lower().strip()

def to_iso(d):
    if pd.isna(d):
        return None
    try:
        return pd.to_datetime(d).date().isoformat()
    except Exception:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(str(d), fmt).date().isoformat()
            except Exception:
                continue
    return None

def as_float(v, default=0.0):
    try:
        s = str(v).replace(",", ".")
        return float(s)
    except Exception:
        return default

def safe_float_with_log(v, context_row, default=0.0):
    s = str(v).replace(",", ".").strip()
    if s == "":
        return 0.0
    try:
        return float(s)
    except Exception:
        print(f"[ALERTE] Montant invalide '{s}' pour ligne: {context_row}")
        return default

def rowkey_call(call_date, base, agent, is_cu, is_don, is_donmail, is_indecis, montant):
    raw = f"{call_date}|{base}|{agent}|{is_cu}|{is_don}|{is_donmail}|{is_indecis}|{montant}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def rowkey_grh(jour, agent, heures):
    raw = f"{jour}|{agent}|{heures}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

# ---------- Détection statuts ----------
_KEYWORDS = {
    "don": [
        r"\bdon\b", r"\bok\b", r"\boui\b", r"\baccepte\b", r"\bvalide\b",
        r"\biban\b", r"\bsepa\b", r"\bprelevement\b", r"\bprlvt\b", r"\bdam\b",
        r"\bdon avec montant\b"
    ],
    "don_mail": [r"\bmail\b", r"\bemail\b", r"\ben ligne\b", r"\bweb\b", r"\blien de don\b"],
    "indecis": [r"\bindecis\b", r"\breflech", r"\brappel\b", r"\bvoir\b"],
    "refus": [r"\brefus\b", r"\bnon\b", r"\bpas interesse\b", r"\braccroche\b", r"\bne veux pas\b"],
}
_COMPILED = {k: [re.compile(p) for p in v] for k, v in _KEYWORDS.items()}

def detect_status(text: str) -> str:
    txt = normalize_text(text)
    if not txt:
        return "inconnu"
    for label, patterns in _COMPILED.items():
        for pat in patterns:
            if pat.search(txt):
                return label
    return "autre"

# ---------- Schéma / Migration ----------
def _table_has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    cur = con.execute(f"PRAGMA table_info({table})")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())

def _ensure_column(con: sqlite3.Connection, table: str, col: str, decl: str, default_sql: str | None = None):
    """Ajoute la colonne si manquante. decl = 'TEXT', 'INTEGER DEFAULT 0', etc."""
    if not _table_has_column(con, table, col):
        sql = f"ALTER TABLE {table} ADD COLUMN {col} {decl}"
        con.execute(sql)
        if default_sql is not None:
            con.execute(f"UPDATE {table} SET {col} = {default_sql} WHERE {col} IS NULL")
        con.commit()
        print(f"[MIGRATION] + colonne {table}.{col} ({decl})")

def _dedup_by_rowkey(con: sqlite3.Connection, table: str):
    cur = con.cursor()
    cur.execute(f"""
        DELETE FROM {table}
         WHERE rowid NOT IN (
           SELECT MIN(rowid) FROM {table}
           GROUP BY row_key
         )
    """)
    con.commit()

def ensure_schema_incremental(con: sqlite3.Connection):
    cur = con.cursor()
    # Crée les tables si absentes (schéma "cible" minimal)
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT
    );
    CREATE TABLE IF NOT EXISTS grh_hours (
        id INTEGER PRIMARY KEY AUTOINCREMENT
    );
    CREATE TABLE IF NOT EXISTS import_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL,
        file_sha1 TEXT NOT NULL,
        imported_at TEXT NOT NULL
    );
    """)
    con.commit()

    # Garantir toutes les colonnes de calls
    _ensure_column(con, "calls", "call_date", "TEXT")
    _ensure_column(con, "calls", "base", "TEXT")
    _ensure_column(con, "calls", "agent", "TEXT")
    _ensure_column(con, "calls", "is_cu", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_don", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_donmail", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_indecis", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "montant", "REAL DEFAULT 0", "0")
    if not _table_has_column(con, "calls", "row_key"):
        con.execute("ALTER TABLE calls ADD COLUMN row_key TEXT")
        con.commit()
        # Backfill row_key existant
        rows = cur.execute("""
            SELECT id, COALESCE(call_date,''), COALESCE(base,''), COALESCE(agent,''),
                   COALESCE(is_cu,0), COALESCE(is_don,0), COALESCE(is_donmail,0),
                   COALESCE(is_indecis,0), COALESCE(montant,0.0)
            FROM calls
            WHERE row_key IS NULL OR row_key=''
        """).fetchall()
        for rid, d, b, a, cu, dn, dm, indec, m in rows:
            rk = rowkey_call(d, b, a, cu, dn, dm, indec, m)
            cur.execute("UPDATE calls SET row_key=? WHERE id=?", (rk, rid))
        con.commit()
        _dedup_by_rowkey(con, "calls")

    # Garantir toutes les colonnes de grh_hours
    _ensure_column(con, "grh_hours", "jour", "TEXT")
    _ensure_column(con, "grh_hours", "agent", "TEXT")
    _ensure_column(con, "grh_hours", "heures", "REAL DEFAULT 0", "0")
    if not _table_has_column(con, "grh_hours", "row_key"):
        con.execute("ALTER TABLE grh_hours ADD COLUMN row_key TEXT")
        con.commit()
        rows = cur.execute("""
            SELECT id, COALESCE(jour,''), COALESCE(agent,''), COALESCE(heures,0.0)
            FROM grh_hours
            WHERE row_key IS NULL OR row_key=''
        """).fetchall()
        for rid, j, a, h in rows:
            rk = rowkey_grh(j, a, h)
            cur.execute("UPDATE grh_hours SET row_key=? WHERE id=?", (rk, rid))
        con.commit()
        _dedup_by_rowkey(con, "grh_hours")

    # Index
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_calls_rowkey ON calls(row_key)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_grh_rowkey ON grh_hours(row_key)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_importlog_sha1 ON import_log(file_sha1)")
    con.commit()

def already_imported(con: sqlite3.Connection, file_path: str) -> bool:
    sha1 = file_sha1(file_path)
    row = con.execute("SELECT 1 FROM import_log WHERE file_sha1=?", (sha1,)).fetchone()
    return bool(row)

def mark_imported(con: sqlite3.Connection, file_path: str):
    sha1 = file_sha1(file_path)
    con.execute("INSERT OR IGNORE INTO import_log(file_path, file_sha1, imported_at) VALUES (?,?,?)",
                (file_path, sha1, datetime.now().isoformat(timespec="seconds")))
    con.commit()

# ---------- Lecture fichiers ----------
SUPPORTED_EXT = {".csv", ".xlsx", ".xls"}

def _should_skip_for_appels(path: str) -> bool:
    name = os.path.basename(path).lower()
    if any(name.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".db", ".pdf"]):
        return True
    if "grh" in name or "extract_grh" in name or "heure" in name or "prod" in name:
        return True
    return False

def _read_table_auto(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"Extension non supportée: {ext}")
    if ext == ".csv":
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
        except Exception:
            return pd.read_csv(path, sep=";", encoding="utf-8-sig")
    else:
        return pd.read_excel(path, engine="openpyxl")

# ---------- Helpers lecture fichiers ----------
def _looks_like_grh(df: pd.DataFrame) -> bool:
    low = [str(c).strip().lower() for c in df.columns]
    # signaux typiques des exports de présence/horaires
    hints = ["heure prod", "heure présence", "agents", "h9 -10h", "h10 -11h", "durée conversation"]
    return any(h in " ".join(low) for h in hints)


def _read_any_table(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(path, engine="openpyxl")
    if ext in [".csv", ".txt"]:
        # try ; then , + encodings
        for sep in [";", ",", "\t"]:
            for enc in ["utf-8-sig", "latin1"]:
                try:
                    return pd.read_csv(path, sep=sep, encoding=enc)
                except Exception:
                    continue
        # dernier essai sans sep (pandas auto)
        return pd.read_csv(path, encoding="utf-8-sig")
    # Fichiers non data : on renvoie DataFrame vide
    return pd.DataFrame()


# ---------- Import Appels (robuste CSV + Excel) ----------
def import_appels_incremental(con: sqlite3.Connection, glob_pattern: str) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[APPELS] Aucun fichier pour {glob_pattern}")
        return 0


    total_new = 0
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        base_name = os.path.basename(path)


        # skip fichiers manifestement non utiles
        if ext in [".db", ".sqlite", ".sqlite3"]:
            print(f"[APPELS] (skip) fichier base de données : {base_name}")
            continue
        if "extract_grh" in base_name.lower():
            print(f"[APPELS] (skip) fichier GRH détecté : {base_name}")
            continue


        try:
            if already_imported(con, path):
                print(f"[APPELS] (skip) déjà importé : {base_name}")
                continue


            df = _read_any_table(path)
        except Exception as e:
            print(f"[APPELS] Erreur lecture {path}: {e}")
            continue


        if df.empty:
            print(f"[APPELS] (skip) vide ou non lisible : {base_name}")
            continue


        # Ignore les fichiers GRH mal dirigés
        if _looks_like_grh(df):
            print(f"[APPELS] (skip) détecté comme GRH : {base_name}")
            continue


        # Normalise colonnes et détecte
        cols_map = {str(c).strip().lower(): c for c in df.columns}
        key_agent = cols_map.get("agent") or cols_map.get("tv") or cols_map.get("agents") or "agent"
        # dates possibles
        key_date = (cols_map.get("date") or cols_map.get("call_date") or cols_map.get("jour")
                    or cols_map.get("date_appel") or cols_map.get("calldate") or "date")
        # base/campagne
        key_base = (cols_map.get("base") or cols_map.get("campagne") or cols_map.get("campaign")
                    or cols_map.get("operation") or "base")
        # statut/typage
        key_type = (cols_map.get("type") or cols_map.get("statut") or cols_map.get("lib_status")
                    or cols_map.get("lib statut") or cols_map.get("status"))
        # montant
        key_montant = (cols_map.get("montant") or cols_map.get("montant_don") or cols_map.get("don")
                       or cols_map.get("amount") or "montant")


        insert_rows = []
        for _, r in df.iterrows():
            agent = norm_str(r.get(key_agent)) if key_agent in df.columns else ""
            call_d = to_iso(r.get(key_date)) if key_date in df.columns else None
            base   = norm_str(r.get(key_base)).upper() if key_base in df.columns else ""


            # Statut → flags
            s_all = norm_str(r.get(key_type)).lower() if key_type in df.columns else ""
            is_donmail = int(any(x in s_all for x in ["don mail", "don en ligne", "email", "lien de don", "paylink"]))
            is_don = 0
            if not is_donmail:
                is_don = int(any(x in s_all for x in ["iban", "sepa", "prélèvement", "prelevement", "dam", "don avec montant", "ok iban", "ok sepa"]))
            is_indecis = int(any(x in s_all for x in ["indecis", "indécis", "rappel", "hesite"]))
            # Un "Cu" = toute issue qualifiée (don, don mail, indécis, refus)
            is_cu = int(bool(is_don or is_donmail or is_indecis or ("refus" in s_all or "ko" in s_all)))


            montant = 0.0
            if key_montant in df.columns:
                montant = as_float(r.get(key_montant), 0.0)


            # Lignes minimales valides
            if not agent or not call_d:
                continue


            rk = rowkey_call(call_d, base, agent, is_cu, is_don, is_donmail, is_indecis, montant)
            insert_rows.append((call_d, base, agent, is_cu, is_don, is_donmail, is_indecis, montant, rk))


        if insert_rows:
            cur = con.cursor()
            cur.executemany("""
                INSERT OR IGNORE INTO calls
                (call_date, base, agent, is_cu, is_don, is_donmail, is_indecis, montant, row_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_rows)
            con.commit()
            added = cur.rowcount if cur.rowcount is not None else 0
            total_new += added
            if added > 0:
                mark_imported(con, path)  # on ne marque que si on a vraiment ajouté
            print(f"[APPELS] +{added} nouvelles lignes depuis {base_name}")
        else:
            print(f"[APPELS] 0 ligne exploitable dans {base_name} (non marqué importé)")


    print(f"[APPELS] ✅ Nouvelles lignes totales: {total_new}")
    return total_new

# ---------- Import GRH ----------
def import_grh_incremental(con: sqlite3.Connection, glob_pattern: str) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[GRH] Aucun fichier pour {glob_pattern}")
        return 0

    total_new = 0
    for path in files:
        try:
            if already_imported(con, path):
                print(f"[GRH] (skip) déjà importé : {os.path.basename(path)}")
                continue
            df = _read_table_auto(path)
        except Exception as e:
            print(f"[GRH] Erreur lecture {path}: {e}")
            continue

        cols = {c.lower().strip(): c for c in df.columns}
        key_date   = cols.get("date") or cols.get("jour") or "date"
        key_agent  = cols.get("agent") or cols.get("tv") or "agent"
        key_heures = cols.get("heures") or cols.get("heur prod") or cols.get("h_prod") or "heures"

        insert_rows = []
        for _, r in df.iterrows():
            jour  = to_iso(r.get(key_date) if key_date in df.columns else None)
            agent = norm_str(r.get(key_agent) if key_agent in df.columns else "")
            h     = as_float(r.get(key_heures) if key_heures in df.columns else 0.0, 0.0)
            if not jour or not agent or h <= 0:
                continue
            rk = rowkey_grh(jour, agent, h)
            insert_rows.append((jour, agent, h, rk))

        if insert_rows:
            cur = con.cursor()
            cur.executemany("""
                INSERT OR IGNORE INTO grh_hours (jour, agent, heures, row_key)
                VALUES (?, ?, ?, ?)
            """, insert_rows)
            con.commit()
            added = cur.rowcount if cur.rowcount is not None else 0
            total_new += added
            print(f"[GRH] +{added} nouvelles lignes depuis {os.path.basename(path)}")
        else:
            print(f"[GRH] 0 ligne exploitable dans {os.path.basename(path)}")

        mark_imported(con, path)

    print(f"[GRH] ✅ Nouvelles lignes totales: {total_new}")
    return total_new

# ---------- Main ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import incrémental appels + GRH (auto CSV/Excel, détection robuste, migration colonnes)")
    ap.add_argument("--db", required=True, help="humanitaire.db ou crm_clients.db")
    ap.add_argument("--appels", required=True, help="Glob des fichiers d'appels (ex: Data\\HIM*.csv ; Data\\IMA*.csv ; Appels*.xlsx)")
    ap.add_argument("--grh",    required=True, help="Glob des fichiers GRH (ex: Data\\extract_grh*.xlsx)")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")

    ensure_schema_incremental(con)

    print(f"[RUN] DB={args.db}")
    n1 = import_appels_incremental(con, args.appels)
    n2 = import_grh_incremental(con, args.grh)

    cur = con.cursor()
    calls = cur.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    hrs   = cur.execute("SELECT COUNT(*) FROM grh_hours").fetchone()[0]
    con.close()
    print(f"[RÉSUMÉ] total calls={calls}, grh_hours={hrs} (ajoutés: appels={n1}, grh={n2})")
