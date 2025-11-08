# etl_incremental.py — Import incrémental quotidien pour humanitaire/CRM
# - Création/migration schéma (tables + colonnes manquantes) + backfill row_key + dédup
# - Journal import (import_log) + index uniques
# - Lecture AUTO CSV/Excel
# - Normalisation + détection robuste statuts (don/don_mail/indécis/refus)
# - Logs pour lignes ignorées et montants invalides

import os, glob, hashlib, sqlite3, argparse, re, unicodedata, csv, shutil
from datetime import datetime
import pandas as pd
import chardet  # pip install chardet si non installé

# ======================================================================
# UTILS GÉNÉRIQUES
# ======================================================================
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
    s = str(d).strip()
    # si format "2025-09-15 14:02:33"
    try:
        return pd.to_datetime(s).date().isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    return None

def as_float(v, default=0.0):
    try:
        s = str(v).replace(",", ".")
        return float(s)
    except Exception:
        return default

def rowkey_call(call_date, base, agent, lib_status, montant):
    raw = f"{call_date}|{base}|{agent}|{lib_status}|{montant}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def rowkey_grh(jour, agent, heures):
    raw = f"{jour}|{agent}|{heures}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

# PETITES FONCTIONS POUR LE DASHBOARD
# =====================================================================
def _parse_date_safe(val: str | None):
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except Exception:
        return None

def query_dashboard(con, start_date: str | None = None, end_date: str | None = None):
    where = ["1=1"]
    params = []
    if start_date:
        where.append("call_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("call_date <= ?")
        params.append(end_date)

    sql = f"""
    WITH base_calls AS (
      SELECT agent,
             SUM(COALESCE(is_cu,0))      AS Cu,
             SUM(COALESCE(is_don,0))     AS Don,
             SUM(COALESCE(is_donmail,0)) AS Don_en_ligne,
             SUM(COALESCE(is_indecis,0)) AS Indecis,
             SUM(COALESCE(montant,0))    AS Montant_Don,
             COUNT(*)                    AS Fich_T
      FROM calls
      WHERE {' AND '.join(where)}
      GROUP BY agent
    ),
    heures AS (
      SELECT agent, SUM(COALESCE(heures,0)) AS Heur_Prod
      FROM grh_hours
      GROUP BY agent
    )
    SELECT
      b.agent AS TV,
      COALESCE(h.Heur_Prod,0.0) AS Heur_Prod,
      b.Cu, b.Don, b.Don_en_ligne AS Don_en_ligne, b.Indecis, b.Montant_Don, b.Fich_T,
      CASE WHEN (b.Don + b.Don_en_ligne)>0
           THEN b.Montant_Don*1.0/(b.Don + b.Don_en_ligne) ELSE 0 END AS Don_Moyen,
      CASE WHEN b.Cu>0 THEN (b.Don + b.Don_en_ligne)*1.0/b.Cu ELSE 0 END AS Tx_daccord,
      CASE WHEN COALESCE(h.Heur_Prod,0)>0 THEN b.Cu*1.0/COALESCE(h.Heur_Prod,0) ELSE 0 END AS Cu_h
    FROM base_calls b
    LEFT JOIN heures h ON h.agent = b.agent
    ORDER BY TV ASC;
    """
    return pd.read_sql_query(sql, con, params=params)

# ======================================================================
# SCHÉMA / MIGRATION (UNE SEULE VERSION)
# ======================================================================
def _table_has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    cur = con.execute(f"PRAGMA table_info({table})")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())

def _ensure_column(con: sqlite3.Connection, table: str, col: str, decl: str, default_sql: str | None = None):
    if not _table_has_column(con, table, col):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        if default_sql is not None:
            con.execute(f"UPDATE {table} SET {col} = {default_sql} WHERE {col} IS NULL")
        con.commit()
        print(f"[MIGRATION] + colonne {table}.{col}")

def _dedup_by_rowkey(con: sqlite3.Connection, table: str):
    con.execute(f"""
        DELETE FROM {table}
        WHERE rowid NOT IN (
          SELECT MIN(rowid) FROM {table} GROUP BY row_key
        )
    """)
    con.commit()

def ensure_schema_incremental(con: sqlite3.Connection):
    """
    Crée les tables minimales et garantit que toutes les colonnes attendues
    par l'import existent (calls, grh_hours, import_log).
    """
    cur = con.cursor()
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

    # calls : toutes les colonnes qu'on utilise vraiment
    _ensure_column(con, "calls", "call_date", "TEXT")
    _ensure_column(con, "calls", "base", "TEXT")
    _ensure_column(con, "calls", "agent", "TEXT")
    _ensure_column(con, "calls", "lib_status", "TEXT")
    _ensure_column(con, "calls", "don_raw", "REAL", "0")
    _ensure_column(con, "calls", "is_cu", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_don", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_donmail", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "is_indecis", "INTEGER DEFAULT 0", "0")
    _ensure_column(con, "calls", "montant", "REAL DEFAULT 0", "0")

    if not _table_has_column(con, "calls", "row_key"):
        _ensure_column(con, "calls", "row_key", "TEXT")
        # backfill
        rows = cur.execute("""
            SELECT id,
                   COALESCE(call_date,''),
                   COALESCE(base,''),
                   COALESCE(agent,''),
                   COALESCE(lib_status,''),
                   COALESCE(montant,0)
            FROM calls
            WHERE row_key IS NULL OR row_key=''
        """).fetchall()
        for rid, d, b, a, ls, m in rows:
            rk = rowkey_call(d, b, a, ls, m)
            cur.execute("UPDATE calls SET row_key=? WHERE id=?", (rk, rid))
        con.commit()
        _dedup_by_rowkey(con, "calls")

    # grh_hours
    _ensure_column(con, "grh_hours", "jour", "TEXT")
    _ensure_column(con, "grh_hours", "agent", "TEXT")
    _ensure_column(con, "grh_hours", "heures", "REAL DEFAULT 0", "0")
    if not _table_has_column(con, "grh_hours", "row_key"):
        _ensure_column(con, "grh_hours", "row_key", "TEXT")
        rows = cur.execute("""
            SELECT id,
                   COALESCE(jour,''),
                   COALESCE(agent,''),
                   COALESCE(heures,0)
            FROM grh_hours
            WHERE row_key IS NULL OR row_key=''
        """).fetchall()
        for rid, j, a, h in rows:
            rk = rowkey_grh(j, a, h)
            cur.execute("UPDATE grh_hours SET row_key=? WHERE id=?", (rk, rid))
        con.commit()
        _dedup_by_rowkey(con, "grh_hours")

    # index
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_calls_rowkey ON calls(row_key)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_grh_rowkey ON grh_hours(row_key)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_importlog_sha1 ON import_log(file_sha1)")
    con.commit()

def ensure_huma_schema(db_path: str):
    """
    Point d'entrée : crée le fichier et lance la migration incrémentale.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_schema_incremental(con)
    con.close()
    print(f"[SCHEMA] Base '{db_path}' verifiée / créée.")


# ======================================================================
# IMPORT LOG
# ======================================================================
def already_imported(con: sqlite3.Connection, file_path: str) -> bool:
    sha1 = file_sha1(file_path)
    row = con.execute("SELECT 1 FROM import_log WHERE file_sha1=?", (sha1,)).fetchone()
    return bool(row)

def mark_imported(con: sqlite3.Connection, file_path: str):
    sha1 = file_sha1(file_path)
    con.execute(
        "INSERT OR IGNORE INTO import_log(file_path, file_sha1, imported_at) VALUES (?,?,?)",
        (file_path, sha1, datetime.now().isoformat(timespec="seconds"))
    )
    con.commit()

# ======================================================================
# HELPERS LECTURE
# ======================================================================
SUPPORTED_EXT = {".csv", ".xlsx", ".xls"}

def _extract_base_from_filename(path: str) -> str:
    # Exemple: IMA5025P_20251006.csv -> IMA5025P
    name = os.path.basename(path)
    base = name.split("_")[0].split(".")[0]
    return base.upper()

# ======================================================================
# IMPORT APPELS (ROBUSTE)
# ======================================================================
def import_appels_incremental(
    con: sqlite3.Connection,
    glob_pattern: str,
    archive_dir: str | None = None,
) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[APPELS] Aucun fichier pour {glob_pattern}")
        return 0

    if archive_dir:
        os.makedirs(archive_dir, exist_ok=True)

    total_new = 0
    for path in files:
        # on s’appuie sur le sha1 du fichier → chaque fichier du jour = identifiant unique
        file_hash = file_sha1(path)
        inserted_for_file = 0

        try:
            with open(path, "rb") as f:
                raw = f.read(50000)
                det = chardet.detect(raw)
                enc = det["encoding"] or "utf-8"

            ext = os.path.splitext(path)[1].lower()
            if ext == ".csv":
                df = pd.read_csv(path, encoding=enc, sep=";", dtype=str, keep_default_na=False)
            else:
                df = pd.read_excel(path, engine="openpyxl", dtype=str)
            df.columns = [c.strip() for c in df.columns]
        except Exception as e:
            print(f"[APPELS] Erreur lecture {path}: {e}")
            continue

        cols = {c.lower(): c for c in df.columns}

        # colonne INDICE (présente partout chez toi)
        key_indice = cols.get("indice")

        # autres colonnes
        key_base = cols.get("base") or cols.get("campagne") or cols.get("nom_base")
        key_agent = (
            cols.get("agent")
            or cols.get("tv")
            or cols.get("operateur")
            or cols.get("agents login")
            or cols.get("agent_login")
            or cols.get("agents_login")
        )
        key_date = (
            cols.get("date")
            or cols.get("call_date")
            or cols.get("jour")
            or cols.get("date appel")
            or cols.get("date_appel")
        )
        key_status = (
            cols.get("lib_status")
            or cols.get("lib statut")
            or cols.get("lib_statut")
            or cols.get("statut")
            or cols.get("status")
        )
        key_montant = cols.get("don") or cols.get("montant") or cols.get("montant_don")

        fallback_base = _extract_base_from_filename(path)

        for idx, r in df.iterrows():
            base = norm_str(r.get(key_base)) if key_base else fallback_base
            agent = norm_str(r.get(key_agent)) if key_agent else "INCONNU"
            date_raw = norm_str(r.get(key_date)) if key_date else ""
            call_date = to_iso(date_raw)
            lib_status = norm_str(r.get(key_status)) if key_status else ""
            montant = as_float(r.get(key_montant), 0.0) if key_montant else 0.0

            # minimum vital
            if not base or not call_date:
                continue

            # ✅ on force le montant uniquement pour ces statuts
            DON_STATUTS_STRICT = (
                "dam don avec montant",
                "don par email",
                "pam pa mensuel",
                "pat pa trimestriel",
            )
            ls_norm = lib_status.lower()
            if ls_norm not in DON_STATUTS_STRICT:
                montant = 0.0

            # ✅ ici on différencie clairement fichiers d'une même date
            if key_indice:
                indice_val = norm_str(r.get(key_indice))
                row_key = hashlib.sha1(f"{file_hash}|{indice_val}".encode("utf-8")).hexdigest()
            else:
                # secours si un jour il y a un fichier sans INDICE
                row_key = hashlib.sha1(
                    f"{call_date}|{base}|{agent}|{lib_status}|{montant}|{idx}".encode("utf-8")
                ).hexdigest()

            # flags (ls_norm déjà défini ci-dessus)
            is_don = int(any(x in ls_norm for x in [
                "don avec montant", "don par email", "pam pa mensuel",
                "pat pa trimestriel", "dam"
            ]))
            is_cu = int(any(x in ls_norm for x in [
                "ref refus", "dam don avec montant", "indécis", "indecis",
                "don par email", "pam pa mensuel", "pat pa trimestriel"
            ]))
            is_donmail = int("mail" in ls_norm or "email" in ls_norm or "en ligne" in ls_norm)
            is_indecis = int("indécis" in ls_norm or "indecis" in ls_norm or "rappel" in ls_norm)

            try:
                con.execute("""
                    INSERT INTO calls
                    (call_date, base, agent, lib_status, don_raw,
                     is_cu, is_don, is_donmail, is_indecis, montant, row_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    call_date, base, agent, lib_status, montant,
                    is_cu, is_don, is_donmail, is_indecis, montant, row_key
                ))
                inserted_for_file += 1
            except sqlite3.IntegrityError:
                # même fichier + même indice → normal
                continue

        con.commit()
        mark_imported(con, path)
        print(f"[APPELS] {os.path.basename(path)} | inserees={inserted_for_file}")
        total_new += inserted_for_file

        if archive_dir and inserted_for_file > 0:
            base_name = os.path.basename(path)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(archive_dir, f"{stamp}_{base_name}")
            try:
                shutil.move(path, dest)
                print(f"[APPELS] Archivé → {dest}")
            except Exception as e:
                print(f"[APPELS] ⚠️ Impossible d'archiver {path}: {e}")

    print(f"[APPELS] ✅ Total insérées≈{total_new}")
    return total_new


# ======================================================================
# IMPORT GRH
# ======================================================================
def _parse_sheet_date(sheet_name: str) -> str | None:
    """
    Les feuilles s'appellent '2025-11-03', '2025-11-04', etc.
    On les transforme en '2025-11-03'
    """
    s = sheet_name.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    return None

def _hms_to_hours(s: str) -> float:
    """
    '03:36:05' -> 3.6014 h
    gère aussi le cas où il y a un ' devant
    """
    if not s or not isinstance(s, str):
        return 0.0
    s = s.strip().strip("'")
    parts = s.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        h, m, sec = [int(x) for x in parts]
        return h + m/60 + sec/3600
    except Exception:
        return 0.0

def import_grh_incremental(con: sqlite3.Connection, glob_pattern: str) -> int:
    files = glob.glob(glob_pattern)
    if not files:
        print(f"[GRH] Aucun fichier trouvé pour {glob_pattern}")
        return 0

    total_new = 0
    for path in files:
        try:
            xls = pd.ExcelFile(path, engine="openpyxl")
        except Exception as e:
            print(f"[GRH] Erreur ouverture {path}: {e}")
            continue

        print(f"[GRH] Lecture fichier: {os.path.basename(path)} ({len(xls.sheet_names)} jours)")

        for sheet_name in xls.sheet_names:
            # 1) la date est dans le nom de la feuille
            jour_iso = _parse_sheet_date(sheet_name)
            if not jour_iso:
                print(f"  ⚠️ Onglet ignoré : '{sheet_name}' (non reconnu comme date)")
                continue

            # 2) les en-têtes sont à la ligne 7 => header=6
            try:
                df = pd.read_excel(xls, sheet_name=sheet_name, header=6)
            except Exception as e:
                print(f"  ⚠️ Erreur lecture '{sheet_name}': {e}")
                continue

            # 3) on cherche exactement “Agents” et “Heure Prod”
            cols = {str(c).strip().lower(): c for c in df.columns}
            key_agent = cols.get("agents")
            key_heure_prod = cols.get("heure prod")

            if not key_agent or not key_heure_prod:
                print(f"  ⚠️ '{sheet_name}': colonnes 'Agents' ou 'Heure Prod' introuvables")
                continue

            inserted_for_sheet = 0
            for _, r in df.iterrows():
                agent = (str(r.get(key_agent)) or "").strip()
                if not agent:
                    continue

                heure_raw = r.get(key_heure_prod)
                # peut être une str "03:36:05"
                if isinstance(heure_raw, str):
                    heures = _hms_to_hours(heure_raw)
                else:
                    # au cas où pandas lit un nombre
                    try:
                        heures = float(heure_raw)
                    except Exception:
                        heures = 0.0

                if heures <= 0:
                    continue

                # row_key pour éviter les doublons
                rk = rowkey_grh(jour_iso, agent, heures)

                try:
                    con.execute("""
                        INSERT INTO grh_hours (jour, agent, heures, row_key)
                        VALUES (?, ?, ?, ?)
                    """, (jour_iso, agent, heures, rk))
                    inserted_for_sheet += 1
                    total_new += 1
                except sqlite3.IntegrityError:
                    # déjà inséré
                    continue

            con.commit()
            print(f"  ✅ {sheet_name} | {inserted_for_sheet} lignes insérées")

    print(f"[GRH] ✅ Total global inséré : {total_new}")
    return total_new

# ======================================================================
# MAIN
# ======================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import incrémental appels + GRH (avec migration row_key)")
    ap.add_argument("--db", required=True, help="humanitaire.db ou crm_clients.db")
    ap.add_argument("--appels", required=True, help="Glob des fichiers d'appels")
    ap.add_argument("--grh",    required=True, help="Glob des fichiers GRH")
    args = ap.parse_args()

    # création / migration
    ensure_huma_schema(args.db)

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")

    print(f"[RUN] DB={args.db}")
    n1 = import_appels_incremental(con, args.appels)
    n2 = import_grh_incremental(con, args.grh)

    # recap
    cur = con.cursor()
    calls = cur.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    hrs   = cur.execute("SELECT COUNT(*) FROM grh_hours").fetchone()[0]
    con.close()
    print(f"[RÉSUMÉ] total calls={calls}, grh_hours={hrs} (ajoutés: appels={n1}, grh={n2})")
