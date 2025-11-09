from __future__ import annotations

# --- Imports Flask & libs ---
from flask import (
    Flask, render_template, request, redirect, session, url_for, flash,
    send_file, jsonify, after_this_request, Response, make_response
)
from flask_socketio import SocketIO, emit
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- Python stdlib / tiers ---
import os
import re
import json
import math
import uuid
import bcrypt
import sqlite3
import hashlib
import tempfile
import unicodedata
import requests
import numpy as np
import pandas as pd
import sys
import subprocess
from io import BytesIO
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

# --- Excel utils ---
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from werkzeug.utils import secure_filename

# --- Projet: modules internes ---
from etl_incremental import (
    ensure_schema_incremental,
    import_appels_incremental,
    import_grh_incremental,
)
from humanitaire import (
    generer_dashboard_humanitaire_df,
    export_dashboard_humanitaire_xlsx,
    extract_tv_list,
    list_bases_disponibles,
    calculer_chiffre_affaire_dyn,
)
from currency_utils import format_local_amount, load_countries
from db_schema import ensure_crm_schema, ensure_primes_table, ensure_huma_schema



# Variables globales simples
chat_history: list[dict] = []


# =====================================================================
# CONFIG
# =====================================================================
SUPPORTED_EXT = {".csv", ".xlsx", ".xls"}


# ---------------------------------------------------------------------------
# .env & Aircall
# ---------------------------------------------------------------------------
load_dotenv()  # charge le fichier .env

# .env doit contenir : AIRCALL_API_ID=xxxx et AIRCALL_API_TOKEN=yyyy
API_ID    = os.getenv("AIRCALL_API_ID", "")
API_TOKEN = os.getenv("AIRCALL_API_TOKEN", "")
if not (API_ID and API_TOKEN):
    print("⚠️  Aircall : variables d'environnement manquantes (AIRCALL_API_ID / AIRCALL_API_TOKEN)")

PRIME_DON_PATH   = os.path.join("data", "Prime don.xlsx")  # place le fichier ici
TAUX_EUR_VERS_DT = 3.30  # ajuste si besoin

def _to_iso(d: str | None, default: date) -> str:
    """Accepte 'YYYY-MM-DD' ou 'DD/MM/YYYY' et renvoie 'YYYY-MM-DD'."""
    if not d:
        return default.isoformat()
    d = d.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            continue
    return default.isoformat()

# ---------------------------------------------------------------------------
# Constantes métiers réutilisables
# ---------------------------------------------------------------------------
CU_STATUSES: tuple[str, ...] = (
    "REF refus",
    "DAM don avec montant",
    "Indécis Don",
    "Don par email",
    "PAM Pa mensuel",
    "PAT Pa Trimestriel",
)

DON_STATUSES: tuple[str, ...] = (
    "DAM don avec montant",
    "Don par email",
    "PAM Pa mensuel",
    "PAT Pa Trimestriel",
)

DON_MAIL_STATUS = "Don par email"

# ---------------------------------------------------------------------------
# Helpers Aircall / numéros
# ---------------------------------------------------------------------------
def _normalize_phone(num: str) -> str:
    return re.sub(r"[^\d+]", "", str(num or "")).strip()

def get_last_aircall_id_by_number(phone_number: str) -> str:
    """
    Récupère l'ID du dernier appel pour un numéro (utile pour alimenter CALL_ID).
    Stratégie : 1) trouver le contact, 2) récupérer ses appels (le plus récent).
    Renvoie '' si rien.
    """
    try:
        # 1) Contact
        contact_url  = "https://api.aircall.io/v1/contacts"
        clean_number = _normalize_phone(phone_number)
        r1 = requests.get(
            contact_url,
            params={"phone_number": clean_number},
            auth=HTTPBasicAuth(API_ID, API_TOKEN),
            timeout=15,
        )
        r1.raise_for_status()
        data1 = r1.json()
        if not data1.get("contacts"):
            return ""

        contact_id = data1["contacts"][0]["id"]

        # 2) Derniers appels du contact
        calls_url = "https://api.aircall.io/v1/calls"
        from_date = (datetime.now() - timedelta(days=90)).isoformat()
        r2 = requests.get(
            calls_url,
            params={"contact_id": contact_id, "order": "desc", "per_page": 1, "from": from_date},
            auth=HTTPBasicAuth(API_ID, API_TOKEN),
            timeout=15,
        )
        r2.raise_for_status()
        calls = r2.json().get("calls", [])
        if not calls:
            return ""
        return str(calls[0].get("id") or "")
    except Exception as e:
        print(f"[get_last_aircall_id_by_number] Erreur: {e}")
        return ""

def update_call_id_in_db(phone_number: str) -> str:
    """
    Récupère le dernier call_id depuis Aircall pour un numéro et met à jour
    la colonne CALL_ID de tous les clients ayant ce numéro mais un CALL_ID vide.
    Retourne le call_id ('' si rien).
    """
    clean = _normalize_phone(phone_number)
    call_id = get_last_aircall_id_by_number(clean)
    if not call_id:
        return ""

    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        # MAJ toutes les lignes avec ce téléphone et CALL_ID vide (en normalisant le numéro)
        c.execute(
            """
            UPDATE clients
               SET CALL_ID = ?
             WHERE REPLACE(REPLACE(REPLACE(TELEPHONE,' ',''),'-',''),'.','') LIKE ?
               AND (CALL_ID IS NULL OR CALL_ID = '')
            """,
            (call_id, f"%{clean}%"),
        )
        conn.commit()
    finally:
        conn.close()
    return call_id




# ---------------------------------------------------------------------------
# Flask app + sécurité / limites
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(32)  # en prod: SECRET_KEY obligatoire

socketio = SocketIO(app)

# Rate limiting (en mémoire pour une instance unique)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Chemins & Config
# ---------------------------------------------------------------------------
DB_NAME = os.getenv("DB_NAME", "crm_clients.db")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join("static", "uploads"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 Mo

# CSRF (utile pour les formulaires)
app.config["WTF_CSRF_ENABLED"] = True
app.config["WTF_CSRF_TIME_LIMIT"] = None
csrf = CSRFProtect(app)

# ======================= Dashboard Humanitaire (safe-format) =======================

# -- Helpers d'analyse/formatage (côté Python, pour éviter les TypeError dans Jinja)

def _parse_date_safe(s: str):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None

# ---------------------------------------------------------
# 2) petit helper pour s'assurer que la table calls
#    a bien les colonnes qu'on veut utiliser
# ---------------------------------------------------------
def _ensure_calls_has_lib_status(conn: sqlite3.Connection):
    """
    Ajoute les colonnes lib_status et don_raw dans calls si elles n'existent pas.
    Ça évite le 'no such column' quand on a recréé la base.
    """
    cur = conn.execute("PRAGMA table_info(calls)")
    cols = {r[1].lower() for r in cur.fetchall()}
    changed = False

    if "lib_status" not in cols:
        conn.execute("ALTER TABLE calls ADD COLUMN lib_status TEXT")
        print("[MIGRATION] + calls.lib_status")
        changed = True

    if "don_raw" not in cols:
        conn.execute("ALTER TABLE calls ADD COLUMN don_raw REAL DEFAULT 0")
        print("[MIGRATION] + calls.don_raw")
        changed = True

    if changed:
        conn.commit()


# ---------------------------------------------------------
# 3) la route dashboard qui applique TA règle CU
# ---------------------------------------------------------



def _format_pct(x):

    try:

        return f"{float(x)*100:.1f}%"

    except Exception:

        return "0.0%"



def _format_hours(x):

    try:

        return f"{float(x):.2f}"

    except Exception:

        return "0.00"



def _format_money(x):

    try:

        return f"{float(x):.2f}"

    except Exception:

        return "0.00"



# Choix robuste du chemin DB (on respecte ta variable si elle existe déjà)

try:

    HUMA_DB_NAME

except NameError:

    # fallback si pas défini plus haut

    HUMA_DB_NAME = os.getenv("HUMA_DB", "humanitaire.db")

DATA_DIR = os.path.join(os.path.dirname(__file__), "Data")
INBOX_APPELS = os.path.join(DATA_DIR, "inbox", "*.csv")
ARCHIVE_APPELS = os.path.join(DATA_DIR, "archive")

# ===================== DASHBOARD HUMANITAIRE (sur kpi_daily_agent) =====================

import io

# ---------- Route Dashboard ----------

@app.route("/dashboard_humanitaire")
def dashboard_humanitaire():
    if "agent_nom" not in session:
        return redirect(url_for("login"))

    # filtres
    date_debut = _parse_date_safe(request.args.get("debut"))
    date_fin   = _parse_date_safe(request.args.get("fin"))
    filtre_asso = request.args.get("asso")  # HIM, IMA, ...

    debut_sql = date_debut.isoformat() if date_debut else "0001-01-01"
    fin_sql   = date_fin.isoformat()   if date_fin   else "9999-12-31"

    db_path = HUMA_DB_NAME

    with sqlite3.connect(db_path) as con:
        # 1) on récupère toutes les bases pour alimenter le filtre
        asso_rows = con.execute("""
            SELECT DISTINCT UPPER(SUBSTR(COALESCE(base,''),1,3)) AS asso
            FROM calls
            WHERE base IS NOT NULL AND base <> ''
            ORDER BY asso
        """).fetchall()
        associations = [r[0] for r in asso_rows if r[0]]

        # 2) on récupère les calls filtrés
        params = [debut_sql, fin_sql]
        extra_where = ""
        if filtre_asso:
            extra_where = " AND UPPER(SUBSTR(COALESCE(base,''),1,3)) = ? "
            params.append(filtre_asso.upper())

        df = pd.read_sql(
            f"""
            SELECT
                COALESCE(base, '(Inconnue)') AS base,
                COALESCE(lib_status, '')     AS lib_status,
                COALESCE(don_raw, montant, 0) AS don_val,
                call_date
            FROM calls
            WHERE call_date >= ? AND call_date <= ?
              {extra_where}
            """,
            con,
            params=params,
        )

        # enlever les lignes sans agent
        df = df[df["base"].notna()]
        df = df[df["base"].str.strip() != ""]

        # heures GRH
        grh = pd.read_sql(
            """
            SELECT COALESCE(heures,0) AS heures
            FROM grh_hours
            WHERE jour >= ? AND jour <= ?
            """,
            con,
            params=(debut_sql, fin_sql),
        )

    total_heures_prod = float(grh["heures"].sum()) if not grh.empty else 0.0

    if df.empty:
        rows_bases = []
        totaux_bases = {"Cu": 0, "Don": 0, "Montant_Don": 0.0, "Don_Moyen": 0.0}
        stats_globales = {
            "total_heures_prod": total_heures_prod,
            "total_cu": 0,
            "total_don": 0,
            "total_don_mail": 0,
            "tx_daccord": 0.0,
            "don_moyen": 0.0,
            "chiffre_affaire": 0.0,
        }
    else:
        df["lib_status_norm"] = df["lib_status"].fillna("").str.strip()
        df["is_cu"] = df["lib_status_norm"].isin(CU_STATUSES).astype(int)
        df["is_don"] = df["lib_status_norm"].isin(DON_STATUSES).astype(int)
        df["is_don_mail"] = (df["lib_status_norm"] == DON_MAIL_STATUS).astype(int)

        def _montant_if_don(row):
            try:
                v = float(row["don_val"])
            except Exception:
                v = 0.0
            return v if row["lib_status_norm"] in DON_STATUSES else 0.0

        df["montant_don"] = df.apply(_montant_if_don, axis=1)

        grp = df.groupby("base", as_index=False).agg(
            Cu=("is_cu", "sum"),
            Don=("is_don", "sum"),
            Montant_Don=("montant_don", "sum"),
        )
        grp["Don Moyen"] = grp.apply(
            lambda r: (r["Montant_Don"] / r["Don"]) if r["Don"] > 0 else 0.0,
            axis=1,
        )
        grp["Tx_d'accord"] = grp.apply(
            lambda r: (r["Don"] / r["Cu"]) if r["Cu"] > 0 else 0.0,
            axis=1,
        )

        rows_bases = grp.sort_values("base").to_dict(orient="records")

        total_cu = int(df["is_cu"].sum())
        total_don = int(df["is_don"].sum())
        total_don_mail = int(df["is_don_mail"].sum())
        total_montant = float(df["montant_don"].sum())
        don_moyen = (total_montant / (total_don + total_don_mail)) if (total_don + total_don_mail) > 0 else 0.0
        tx_daccord = ((total_don + total_don_mail) / total_cu) if total_cu > 0 else 0.0

        # chiffre d'affaire estimé
        ca = calculer_chiffre_affaire_dyn(
            don_moyen=don_moyen,
            tx_daccord=tx_daccord,
            total_don=total_don,
            path_excel=os.path.join("Data", "inbox", "BDD QUALICONTACT.xlsx")
        )

        totaux_bases = {
            "Cu": int(grp["Cu"].sum()),
            "Don": int(grp["Don"].sum()),
            "Montant_Don": float(grp["Montant_Don"].sum()),
            "Don_Moyen": (float(grp["Montant_Don"].sum()) / int(grp["Don"].sum()))
            if int(grp["Don"].sum()) > 0 else 0.0,
        }

        stats_globales = {
            "total_heures_prod": total_heures_prod,
            "total_cu": total_cu,
            "total_don": total_don,
            "total_don_mail": total_don_mail,
            "tx_daccord": tx_daccord,
            "don_moyen": don_moyen,
            "chiffre_affaire": ca,
        }

    return render_template(
        "dashboard_humanitaire.html",
        rows_bases=rows_bases,
        totaux_bases=totaux_bases,
        stats_globales=stats_globales,
        associations=associations,   # ✅ envoyé au template
        asso_selected=filtre_asso,   # ✅ pour garder la sélection
        debut=(date_debut.isoformat() if date_debut else ""),
        fin=(date_fin.isoformat() if date_fin else ""),
        auj=date.today().isoformat(),
    )

@app.route("/dashboard_huma_agents_stats")
def dashboard_huma_agents_stats():
    if "agent_nom" not in session:
        return redirect(url_for("login"))

    # -------- filtres --------
    today = date.today()
    first_day = today.replace(day=1)

    # dates (si pas fournies → mois en cours)
    date_debut = _parse_date_safe(request.args.get("debut")) or first_day
    date_fin   = _parse_date_safe(request.args.get("fin")) or today

    # filtre agent (peut être vide)
    agent_filtre = request.args.get("agent")

    debut_sql = date_debut.isoformat()
    fin_sql   = date_fin.isoformat()

    db_path = HUMA_DB_NAME

    with sqlite3.connect(db_path) as con:
        # liste des agents pour le select
        ag_rows = con.execute("""
            SELECT DISTINCT agent
            FROM calls
            WHERE agent IS NOT NULL AND agent <> ''
            ORDER BY agent
        """).fetchall()
        agents = [r[0] for r in ag_rows]

        # requête appels
        params = [debut_sql, fin_sql]
        extra = ""
        if agent_filtre:
            extra = " AND agent = ? "
            params.append(agent_filtre)

        df = pd.read_sql(f"""
            SELECT
                COALESCE(agent,'(Inconnu)') AS agent,
                COALESCE(lib_status,'')     AS lib_status,
                COALESCE(don_raw, montant, 0) AS don_val,
                call_date
            FROM calls
            WHERE call_date >= ? AND call_date <= ?
              {extra}
            """,
            con,
            params=params
        )

        # heures GRH
        grh = pd.read_sql("""
            SELECT agent, jour, heures
            FROM grh_hours
            WHERE jour >= ? AND jour <= ?
            """,
            con,
            params=(debut_sql, fin_sql)
        )

    # si pas d'appels → on renvoie vide
    if df.empty:
        return render_template(
            "dashboard_huma_agents_stats.html",
            agents=agents,
            agent_selected=agent_filtre,
            daily_rows=[],
            monthly_rows=[],
            debut=date_debut.isoformat(),
            fin=date_fin.isoformat(),
            auj=today.isoformat(),
        )

    # enrichissement
    df["lib_status_norm"] = df["lib_status"].fillna("").str.strip()
    df["is_cu"] = df["lib_status_norm"].isin(CU_STATUSES).astype(int)
    df["is_don"] = df["lib_status_norm"].isin(DON_STATUSES).astype(int)
    df["is_don_mail"] = (df["lib_status_norm"] == DON_MAIL_STATUS).astype(int)

    def _montant_if_don(row):
        try:
            v = float(row["don_val"])
        except Exception:
            v = 0.0
        return v if row["lib_status_norm"] in DON_STATUSES else 0.0

    df["montant_don"] = df.apply(_montant_if_don, axis=1)

    # daily
    daily = df.groupby(["agent", "call_date"], as_index=False).agg(
        Cu=("is_cu", "sum"),
        Don=("is_don", "sum"),
        DonMail=("is_don_mail", "sum"),
        Montant_Don=("montant_don", "sum"),
    )

    # merge heures
    if not grh.empty:
        daily = daily.merge(
            grh.rename(columns={"jour": "call_date"}),
            on=["agent", "call_date"],
            how="left"
        )
    if "heures" not in daily.columns:
        daily["heures"] = 0.0

    # enlever les lignes sans agent
    daily = daily[daily["agent"].notna()]
    daily = daily[daily["agent"].str.strip() != ""]

    daily_rows = []
    for _, r in daily.iterrows():
        total_dons_ligne = int(r["Don"])
        don_moyen = (r["Montant_Don"] / total_dons_ligne) if total_dons_ligne > 0 else 0.0
        tx_daccord = (total_dons_ligne / r["Cu"]) if r["Cu"] > 0 else 0.0

        ca = calculer_chiffre_affaire_dyn(
            don_moyen=don_moyen,
            tx_daccord=tx_daccord,
            total_don=total_dons_ligne,
            path_excel=os.path.join("Data", "inbox", "BDD QUALICONTACT.xlsx")
        )

        daily_rows.append({
            "agent": r["agent"],
            "date": r["call_date"],
            "heures": float(r["heures"] or 0),
            "Cu": int(r["Cu"]),
            "Don": int(r["Don"]),
            "DonMail": int(r["DonMail"]),
            "Montant_Don": float(r["Montant_Don"]),
            "don_moyen": don_moyen,
            "tx_daccord": tx_daccord,
            "ca": ca,
        })

    # mensuel (sur la même période)
    df["month"] = df["call_date"].str.slice(0, 7)
    monthly = df.groupby(["agent", "month"], as_index=False).agg(
        Cu=("is_cu", "sum"),
        Don=("is_don", "sum"),
        DonMail=("is_don_mail", "sum"),
        Montant_Don=("montant_don", "sum"),
    )

    monthly_rows = []
    for _, r in monthly.iterrows():
        total_dons_ligne = int(r["Don"])
        don_moyen = (r["Montant_Don"] / total_dons_ligne) if total_dons_ligne > 0 else 0.0
        tx_daccord = (total_dons_ligne / r["Cu"]) if r["Cu"] > 0 else 0.0

        ca = calculer_chiffre_affaire_dyn(
            don_moyen=don_moyen,
            tx_daccord=tx_daccord,
            total_don=total_dons_ligne,
            path_excel=os.path.join("Data", "inbox", "BDD QUALICONTACT.xlsx")
        )

        monthly_rows.append({
            "agent": r["agent"],
            "month": r["month"],
            "Cu": int(r["Cu"]),
            "Don": int(r["Don"]),
            "DonMail": int(r["DonMail"]),
            "Montant_Don": float(r["Montant_Don"]),
            "don_moyen": don_moyen,
            "tx_daccord": tx_daccord,
            "ca": ca,
        })

    return render_template(
        "dashboard_huma_agents_stats.html",
        agents=agents,
        agent_selected=agent_filtre,
        daily_rows=daily_rows,
        monthly_rows=monthly_rows,
        debut=date_debut.isoformat(),
        fin=date_fin.isoformat(),
        auj=today.isoformat(),
    )


@app.route("/run_etl", methods=["POST"])
def run_etl():
    """
    Relance le script etl_incremental.py pour importer les nouvelles bases CSV et GRH XLSX.
    """
    try:
        db_path = HUMA_DB_NAME
        appels_glob = os.path.join("Data", "inbox", "*.csv")
        grh_glob = os.path.join("Data", "*.xlsx")

        # Lancement du script ETL dans un sous-processus
        cmd = [
            sys.executable,  # python actuel
            "etl_incremental.py",
            "--db", db_path,
            "--appels", appels_glob,
            "--grh", grh_glob
        ]
        print(f"[ETL] ▶️ Lancement de : {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        # Log dans la console + retour au front
        print("[ETL] stdout:\n", result.stdout)
        print("[ETL] stderr:\n", result.stderr)

        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Import terminé avec succès.", "log": result.stdout}), 200
        else:
            return jsonify({"status": "error", "message": "Erreur lors de l'import.", "log": result.stderr}), 500

    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "L'import a dépassé le délai autorisé."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------- Exports (CSV & Excel) ----------

@app.route("/export_kpi_csv")

def export_kpi_csv():

    if "agent_nom" not in session:

        return redirect(url_for("login"))



    date_debut = _parse_date_safe(request.args.get("debut"))

    date_fin   = _parse_date_safe(request.args.get("fin"))

    base_filtre  = (request.args.get("base") or "").strip()

    agent_filtre = (request.args.get("agent") or "").strip()

    debut_sql = date_debut.isoformat() if date_debut else "0001-01-01"

    fin_sql   = date_fin.isoformat()   if date_fin   else "9999-12-31"



    where = ["jour >= ? AND jour <= ?"]

    params = [debut_sql, fin_sql]

    if base_filtre:

        where.append("base = ?")

        params.append(base_filtre)

    if agent_filtre:

        where.append("agent = ?")

        params.append(agent_filtre)

    where_sql = " AND ".join(where)



    with sqlite3.connect(HUMA_DB_NAME) as con:

        con.row_factory = sqlite3.Row

        df = pd.read_sql(f"""

            SELECT jour, agent, base,

                   Cu, Don, Don_en_ligne, Indecis, Montant_Don, Fich_T,

                   Heur_Prod, Don_Moyen, Tx_accord, Cu_H, Tx_Argu

            FROM kpi_daily_agent

            WHERE {where_sql}

            ORDER BY agent COLLATE NOCASE, jour

        """, con, params=params)



    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")

    return Response(

        csv_bytes,

        mimetype="text/csv",

        headers={"Content-Disposition": f"attachment; filename=kpi_{debut_sql}_{fin_sql}.csv"}

    )



@app.route("/export_kpi_xlsx")

def export_kpi_xlsx():

    if "agent_nom" not in session:

        return redirect(url_for("login"))



    date_debut = _parse_date_safe(request.args.get("debut"))

    date_fin   = _parse_date_safe(request.args.get("fin"))

    base_filtre  = (request.args.get("base") or "").strip()

    agent_filtre = (request.args.get("agent") or "").strip()

    debut_sql = date_debut.isoformat() if date_debut else "0001-01-01"

    fin_sql   = date_fin.isoformat()   if date_fin   else "9999-12-31"



    where = ["jour >= ? AND jour <= ?"]

    params = [debut_sql, fin_sql]

    if base_filtre:

        where.append("base = ?")

        params.append(base_filtre)

    if agent_filtre:

        where.append("agent = ?")

        params.append(agent_filtre)

    where_sql = " AND ".join(where)



    with sqlite3.connect(HUMA_DB_NAME) as con:

        df = pd.read_sql(f"""

            SELECT jour, agent, base,

                   Cu, Don, Don_en_ligne, Indecis, Montant_Don, Fich_T,

                   Heur_Prod, Don_Moyen, Tx_accord, Cu_H, Tx_Argu

            FROM kpi_daily_agent

            WHERE {where_sql}

            ORDER BY agent COLLATE NOCASE, jour

        """, con, params=params)



    # Écriture en mémoire

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:

        df.to_excel(writer, index=False, sheet_name="KPIs")

    output.seek(0)



    return send_file(

        output,

        as_attachment=True,

        download_name=f"kpi_{debut_sql}_{fin_sql}.xlsx",

        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    )

@app.route("/dashboard_huma_agents")
def dashboard_huma_agents():
    if "agent_nom" not in session:
        return redirect(url_for("login"))

    date_debut = _parse_date_safe(request.args.get("debut"))
    date_fin   = _parse_date_safe(request.args.get("fin"))
    sort_key   = request.args.get("sort", "Don")       # clé de tri
    sort_dir   = request.args.get("dir", "desc")       # asc / desc
    export     = request.args.get("export") == "csv"   # export ?

    debut_sql = date_debut.isoformat() if date_debut else "0001-01-01"
    fin_sql   = date_fin.isoformat()   if date_fin   else "9999-12-31"
    db_path   = HUMA_DB_NAME

    with sqlite3.connect(db_path) as con:
        calls_df = pd.read_sql(
            """
            SELECT
                COALESCE(agent, 'INCONNU') as agent,
                COALESCE(lib_status, '')   as lib_status,
                COALESCE(don_raw, montant, 0) as don_val,
                call_date
            FROM calls
            WHERE call_date >= ? AND call_date <= ?
            """,
            con,
            params=(debut_sql, fin_sql),
        )
        grh_df = pd.read_sql(
            """
            SELECT
                COALESCE(agent, 'INCONNU') as agent,
                COALESCE(heures, 0) as heures,
                COALESCE(jour, '') as jour
            FROM grh_hours
            WHERE (jour >= ? AND jour <= ?) OR jour IS NULL OR jour = ''
            """,
            con,
            params=(debut_sql, fin_sql),
        )

    if calls_df.empty:
        rows_agents = []
    else:
        calls_df["lib_status_norm"] = calls_df["lib_status"].fillna("").str.strip()

        calls_df["is_cu"]       = calls_df["lib_status_norm"].isin(CU_STATUSES).astype(int)
        calls_df["is_don"]      = calls_df["lib_status_norm"].isin(DON_STATUSES).astype(int)
        calls_df["is_don_mail"] = (calls_df["lib_status_norm"] == DON_MAIL_STATUS).astype(int)
        calls_df["is_indecis"]  = calls_df["lib_status_norm"].str.contains("indécis", case=False).astype(int)

        def _montant_if_don(row):
            try:
                val = float(row["don_val"])
            except Exception:
                val = 0.0
            return val if row["lib_status_norm"] in DON_STATUSES else 0.0

        calls_df["montant_don"] = calls_df.apply(_montant_if_don, axis=1)

        agg_calls = calls_df.groupby("agent", as_index=False).agg(
            Cu=("is_cu", "sum"),
            Don=("is_don", "sum"),
            Indecis=("is_indecis", "sum"),
            Don_par_mail=("is_don_mail", "sum"),
            Montant=("montant_don", "sum"),
        )

        if grh_df.empty:
            agg_grh = pd.DataFrame({"agent": [], "Heur_de_prod": []})
        else:
            agg_grh = grh_df.groupby("agent", as_index=False).agg(
                Heur_de_prod=("heures", "sum")
            )

        df = agg_calls.merge(agg_grh, on="agent", how="left")
        df["Heur_de_prod"] = df["Heur_de_prod"].fillna(0.0)

        df["Tx_daccord"] = df.apply(
            lambda r: ((r["Don"] + r["Don_par_mail"]) / r["Cu"]) if r["Cu"] > 0 else 0.0,
            axis=1,
        )
        df["Don_moyen"] = df.apply(
            lambda r: (r["Montant"] / (r["Don"] + r["Don_par_mail"]))
            if (r["Don"] + r["Don_par_mail"]) > 0 else 0.0,
            axis=1,
        )
        df["Cu_h"] = df.apply(
            lambda r: (r["Cu"] / r["Heur_de_prod"]) if r["Heur_de_prod"] > 0 else 0.0,
            axis=1,
        )

        # tri
        if sort_key in df.columns:
            df = df.sort_values(
                by=sort_key,
                ascending=(sort_dir == "asc"),
                kind="mergesort"  # stable
            )
        else:
            df = df.sort_values(by="Don", ascending=False)

        # export CSV ?
        if export:
            csv_data = df.to_csv(index=False)
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": "attachment; filename=stats_agents.csv"
                }
            )

        rows_agents = df.to_dict(orient="records")

    return render_template(
        "dashboard_huma_agents.html",
        rows_agents=rows_agents,
        debut=(date_debut.isoformat() if date_debut else ""),
        fin=(date_fin.isoformat() if date_fin else ""),
        auj=date.today().isoformat(),
        sort=sort_key,
        dir=sort_dir,
    )

# ---------------------------------------------------------------------------
# Auto-migration SQLite : colonne PAYS_CODE / photo / TV dans agents
# ---------------------------------------------------------------------------
COUNTRIES = load_countries()  # [{'code':'FR','nom':'France',...}, ...]
COUNTRY_NAME_BY_CODE = {c["code"]: c["nom"] for c in COUNTRIES}

def _is_valid_country_code(code: str) -> bool:
    if not code:
        return False
    code = code.strip().upper()
    return any(c["code"] == code for c in COUNTRIES)

# --- Boot: migrations & schémas ---
print(f"[BOOT] DB_NAME utilisé = {DB_NAME}")
ensure_crm_schema(DB_NAME)
ensure_primes_table(DB_NAME)
ensure_huma_schema(HUMA_DB_NAME)

# ---------------------------------------------------------------------------
# Filtres & context Jinja
# ---------------------------------------------------------------------------
@app.template_filter("nan_to_empty")
def nan_to_empty(v):
    try:
        if v is None:
            return ""
        if isinstance(v, float) and math.isnan(v):
            return ""
        return v
    except Exception:
        return ""

@app.template_filter("monnaie")
def monnaie_filter(montant_eur, pays="France", taux=1.0):
    """
    Exemple d'usage : {{ 150 | monnaie('Tunisie', 3.35) }}
    """
    try:
        return format_local_amount(pays, float(montant_eur or 0), float(taux))
    except Exception:
        # Fallback simple si erreur
        return f"{montant_eur} €"

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=lambda: generate_csrf())

# ---------------------------------------------------------------------------
# Création des tables principales CRM (clients, agents, etc.)
# ---------------------------------------------------------------------------
def get_agents() -> list[str]:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT NOM FROM agents")
    agents = [row[0] for row in c.fetchall()]
    conn.close()
    return agents

def get_campagnes():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, nom FROM campagnes")
    campagnes = c.fetchall()
    conn.close()
    return campagnes

def get_campagne_id_by_name(nom: str) -> int | None:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM campagnes WHERE nom = ?", (nom,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def _slugify_login_from_tv(tv: str) -> str:
    # "38Assia F" -> "38assia.f"
    s = unicodedata.normalize("NFKD", tv).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", ".", s)     # non alphanum -> points
    s = re.sub(r"\.+", ".", s).strip(".")    # évite .. et . de début/fin
    return s.lower()

def _ensure_unique_login(base_login: str, existing_logins: set[str]) -> str:
    login = base_login
    i = 2
    while login in existing_logins:
        login = f"{base_login}{i}"
        i += 1
    return login

# ──────────────────────────────────────────────────────────────────────────────
# Authentification
# ──────────────────────────────────────────────────────────────────────────────
# ------------------------- LOGIN (DEBUG) -------------------------
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"], error_message="Trop de tentatives. Réessayez dans une minute.")
def login():
    """
    Route de connexion sécurisée avec compatibilité bcrypt et session cohérente.
    """
    try:
        print("\n[LOGIN] -------------------------------")
        print(f"[LOGIN] DB_NAME utilisé = {DB_NAME}")

        # Affichage du formulaire
        if request.method == 'GET':
            print("[LOGIN] GET -> rendu du formulaire")
            return render_template('login.html')

        # Si déjà connecté, redirige vers l'accueil
        if 'agent_nom' in session:
            print(f"[LOGIN] Déjà connecté en tant que {session.get('agent_nom')}, redirection index")
            return redirect(url_for('index'))

        # Champs du formulaire
        login_form = (request.form.get('LOGIN') or '').strip()
        mdp_saisi = request.form.get('MDP') or ''
        print(f"[LOGIN] POST -> LOGIN='{login_form}', MDP fourni ? {'oui' if mdp_saisi else 'non'}")

        if not login_form or not mdp_saisi:
            flash("Veuillez saisir votre identifiant et votre mot de passe.", "error")
            return render_template('login.html'), 400

        # Recherche de l'utilisateur
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT NOM, ROLE, MDP, campagne_id FROM agents WHERE LOGIN = ?", (login_form,))
        row = c.fetchone()
        conn.close()

        if not row:
            flash("Identifiant inconnu.", "error")
            print(f"[LOGIN] Aucun agent trouvé pour LOGIN='{login_form}'")
            return render_template('login.html'), 200

        nom_agent, role_agent, hash_en_base, campagne_id = row
        print(f"[LOGIN] Agent trouvé: NOM={nom_agent}, ROLE={role_agent}, campagne_id={campagne_id}")

        # Préparation du hash
        if isinstance(hash_en_base, (bytes, bytearray)):
            hash_bytes = hash_en_base
        else:
            hash_bytes = str(hash_en_base or "").encode('utf-8')

        # Détection du format de mot de passe
        is_bcrypt = hash_bytes.startswith(b"$2a$") or hash_bytes.startswith(b"$2b$") or hash_bytes.startswith(b"$2y$")
        print(f"[LOGIN] Mot de passe en base: {'bcrypt' if is_bcrypt else 'clair/format_inconnu'}")

        # Vérification
        ok = False
        if is_bcrypt:
            try:
                ok = bcrypt.checkpw(mdp_saisi.encode('utf-8'), hash_bytes)
            except Exception as e:
                print(f"[LOGIN] Erreur checkpw bcrypt: {e}")
                ok = False
        else:
            ok = (mdp_saisi == hash_bytes.decode('utf-8', errors='ignore'))

        print(f"[LOGIN] Vérification OK ? {ok}")

        if not ok:
            flash("Mot de passe incorrect.", "error")
            return render_template('login.html'), 200

        # Succès : création de la session cohérente
        session['agent_nom'] = nom_agent
        session['agent_role'] = role_agent   # ← AJOUT pour corriger ton KeyError
        session['role'] = role_agent         # ← conserve l'ancien nom pour compatibilité
        session['campagne_id'] = campagne_id
        flash("Connexion réussie.", "success")
        print(f"[LOGIN] Succès -> session posée pour {nom_agent} ({role_agent}), redirection index")

        return redirect(url_for('index'))

    except Exception as e:
        print(f"[LOGIN][ERREUR] {e}")
        flash("Erreur serveur pendant la connexion.", "error")
        return render_template('login.html'), 500
# ----------------------- FIN LOGIN (VERSION STABLE) -----------------------


@app.route('/logout')
def logout():
    if 'agent_nom' in session:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "INSERT INTO journal_connexions (agent_nom, date_connexion, page, type_event) VALUES (?, ?, ?, ?)",
            (session['agent_nom'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), '/logout', 'deconnexion')
        )
        conn.commit()
        conn.close()
    session.clear()
    flash("Déconnexion réussie.", "info")
    return redirect(url_for('login'))

# ──────────────────────────────────────────────────────────────────────────────
# Formulaires d'ajout
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def index():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    campagne_id = session.get('campagne_id', 1)
    hum_id = get_campagne_id_by_name("HUMANITAIRE")

    if campagne_id == 2:
        # VALANDRE
        return redirect(url_for('formulaire_valandre'))
    elif hum_id and campagne_id == hum_id:
        # HUMANITAIRE → on les envoie direct vers le dashboard Humanitaire
        return redirect(url_for('dashboard_humanitaire'))
    elif campagne_id != 1:
        # toute autre campagne non gérée
        flash("Accès interdit à ce formulaire.", "danger")
        return redirect(url_for('login'))

    # s'assure que TYPE_OFFRE existe
    conn_check = sqlite3.connect(DB_NAME)
    c_check = conn_check.cursor()
    try:
        c_check.execute("ALTER TABLE clients ADD COLUMN TYPE_OFFRE TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn_check.commit()
    conn_check.close()

    agents = get_agents()
    date_auj = datetime.now().strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT nom FROM campagnes WHERE id = ?", (campagne_id,))
    campagne_row = c.fetchone()
    conn.close()
    campagne_nom = campagne_row[0] if campagne_row else "EXOSPHERE_SFR"

    if request.method == 'GET':
        if campagne_nom == "VALANDRE":
            return render_template('formulaire_valandre.html', agents=agents, agent_nom=session['agent_nom'], agent_role=session['agent_role'], date_auj=date_auj)
        else:
            return render_template('formulaire.html', agents=agents, agent_nom=session['agent_nom'], agent_role=session['agent_role'], date_auj=date_auj)

    # POST : enregistrement du client
    if request.method == 'POST':
        campagne_id = session.get('campagne_id', 1)
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT nom FROM campagnes WHERE id = ?", (campagne_id,))
        campagne_row = c.fetchone()
        conn.close()
        campagne_nom = campagne_row[0] if campagne_row else "EXOSPHERE_SFR"

        if campagne_nom == "VALANDRE":
            # Valandre
            produits = ["STRATO", "LSR", "PRESSE", "ENI", "SERENITY", "PROTEC_ALLIANCE", "WEKIWI"]
            data_dict = {}
            for prod in produits:
                data_dict[f"{prod}_NUM"] = request.form.get(f"{prod}_NUM", "")
                data_dict[f"{prod}_STATUT"] = request.form.get(f"{prod}_STATUT", "")
                data_dict[f"{prod}_REMARQUE"] = request.form.get(f"{prod}_REMARQUE", "")
            data = (
                request.form['DATE_SIGNATURE'],
                request.form['NOM_VENDEUR'],
                request.form['PRENOM_VENDEUR'],
                request.form['TITRE'],
                request.form['NOM_CLIENT'],
                request.form['PRENOM_CLIENT'],
                request.form['TELEPHONE'],
                request.form.get('AGENT', session['agent_nom']),
                request.form.get('EXTRANET', ''),
                *[data_dict[f"{prod}_NUM"] for prod in produits],
                *[data_dict[f"{prod}_STATUT"] for prod in produits],
                *[data_dict[f"{prod}_REMARQUE"] for prod in produits],
                session['agent_nom'],
                session['agent_nom'],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                campagne_id
            )
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute(f"""
                INSERT INTO clients (
                    DATE_SIGNATURE, NOM_VENDEUR, PRENOM_VENDEUR, TITRE,
                    NOM_CLIENT, PRENOM_CLIENT, TELEPHONE, AGENT, EXTRANET,
                    {', '.join([f"{prod}_NUM" for prod in produits])},
                    {', '.join([f"{prod}_STATUT" for prod in produits])},
                    {', '.join([f"{prod}_REMARQUE" for prod in produits])},
                    CREE_PAR, MODIFIE_PAR, DATE_MODIF, campagne_id
                ) VALUES ({','.join(['?']*(9 + 3*len(produits) + 4))})
            """, data)
            conn.commit()
            conn.close()
            flash("Client VALANDRE ajouté avec succès !", "success")
            return redirect('/dashboard')

        else:
            # SFR
            numero = request.form['TELEPHONE']
            call_id = get_last_aircall_id_by_number(numero)
            deuxieme_adresse = request.form.get('DEUXIEME_ADRESSE', '').strip()
            troisieme_adresse = request.form.get('TROISIEME_ADRESSE', '').strip()
            type_offre = request.form.get('TYPE_OFFRE', '').strip()
            nom_vendeur = request.form.get('NOM_VENDEUR', '').strip()
            prenom_vendeur = request.form.get('PRENOM_VENDEUR', '').strip()
            telephone_vendeur = request.form.get('TELEPHONE_VENDEUR', '').strip()

            if session['agent_role'] == 'admin':
                date_signature = request.form['DATE_SIGNATURE']
                agent_client = request.form['AGENT']
            else:
                date_signature = datetime.now().strftime('%Y-%m-%d')
                agent_client = session['agent_nom']

            date_courante = datetime.strptime(date_signature, "%Y-%m-%d")
            mois_courant = date_courante.month
            annee_courante = date_courante.year

            # Doublons SFR
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("""
                SELECT DATE_SIGNATURE, AGENT, COALESCE(DEUXIEME_ADRESSE,''), COALESCE(TROISIEME_ADRESSE,''), TYPE_OFFRE
                FROM clients
                WHERE TELEPHONE = ? AND STATUT = 'valide'
            """, (numero,))
            doublons = c.fetchall()
            conn.close()

            for date_exist, agent_ancien, adresse2_exist, adresse3_exist, type_offre_exist in doublons:
                try:
                    date_exist_dt = datetime.strptime(date_exist, "%Y-%m-%d")
                    if type_offre.lower() == "mobile":
                        if (type_offre_exist and type_offre_exist.lower() == "mobile" and date_exist_dt.date() == date_courante.date()):
                            flash(f"⚠️ Client Mobile déjà existant saisi par {agent_ancien} pour aujourd'hui.", "danger")
                            return render_template('formulaire.html', agents=agents, agent_nom=session['agent_nom'], agent_role=session['agent_role'], date_auj=date_auj, doublon=True, ancien_agent=agent_ancien, form=request.form)
                        continue
                    if ((not type_offre_exist or type_offre_exist.lower() != "mobile")
                        and date_exist_dt.month == mois_courant
                        and date_exist_dt.year == annee_courante
                        and deuxieme_adresse == "" and troisieme_adresse == ""
                        and adresse2_exist == "" and adresse3_exist == ""):
                        flash(f"⚠️ Client déjà existant saisi par {agent_ancien} pour le même mois.", "danger")
                        return render_template('formulaire.html', agents=agents, agent_nom=session['agent_nom'], agent_role=session['agent_role'], date_auj=date_auj, doublon=True, ancien_agent=agent_ancien, form=request.form)
                except Exception:
                    continue

            # Insert SFR
            champs_insert = [
                'DATE_SIGNATURE','CIVILITE_CLIENT','NOM_CLIENT','PRENOM_CLIENT','TELEPHONE','STATUT','AGENT',
                'DEUXIEME_ADRESSE','TROISIEME_ADRESSE','CREE_PAR','MODIFIE_PAR','DATE_MODIF','campagne_id',
                'NOM_VENDEUR','PRENOM_VENDEUR','TELEPHONE_VENDEUR','TYPE_OFFRE','CALL_ID'
            ]
            data = (
                date_signature, request.form['CIVILITE_CLIENT'], request.form['NOM_CLIENT'], request.form['PRENOM_CLIENT'],
                numero, request.form['STATUT'], agent_client, deuxieme_adresse, troisieme_adresse,
                session['agent_nom'], session['agent_nom'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), campagne_id,
                nom_vendeur, prenom_vendeur, telephone_vendeur, type_offre, call_id
            )
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            placeholders = ', '.join(['?'] * len(champs_insert))
            sql = f"INSERT INTO clients ({', '.join(champs_insert)}) VALUES ({placeholders})"
            c.execute(sql, data)
            conn.commit()
            conn.close()
            notifier_nouveau_client(request.form['NOM_CLIENT'])
            flash("Client ajouté avec succès !", "success")
            return redirect('/dashboard')

# ──────────────────────────────────────────────────────────────────────────────
# Dashboards
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    if session.get('agent_role') not in ['admin', 'superviseur'] and session.get('campagne_id') != 1:
        flash("Accès interdit à ce dashboard.", "danger")
        return redirect(url_for('index'))

    recherche = request.args.get('recherche', '').strip()
    statut = request.args.get('statut', '').strip()
    agent = request.args.get('agent', '').strip()
    date_debut = request.args.get('date_debut', '').strip()
    date_fin = request.args.get('date_fin', '').strip()
    type_offre = request.args.get('type_offre', '').strip()

    auj = datetime.now().strftime('%Y-%m-%d')

    try:
        page = int(request.args.get('page', 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    par_page = 20
    offset = (page - 1) * par_page

    if not recherche and not statut and not agent and not date_debut and not date_fin and not type_offre:
        date_debut = date_fin = auj

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT id FROM campagnes WHERE nom = 'EXOSPHERE_SFR'")
    row = c.fetchone()
    campagne_id = row[0] if row else 1

    # Photo profil
    c.execute("SELECT photo FROM agents WHERE NOM = ?", (session['agent_nom'],))
    photo_row = c.fetchone()
    photo_filename = photo_row[0] if photo_row and photo_row[0] else None
    if photo_filename:
        agent_photo = url_for('static', filename='uploads/' + photo_filename)
    else:
        agent_photo = url_for('static', filename='img/avatar.png')

    c.execute("SELECT NOM FROM agents")
    agents = [row[0] for row in c.fetchall()]

    sql = """SELECT id, DATE_SIGNATURE, CIVILITE_CLIENT, NOM_CLIENT, PRENOM_CLIENT, TELEPHONE, STATUT, AGENT, DEUXIEME_ADRESSE, TROISIEME_ADRESSE, CREE_PAR, MODIFIE_PAR, DATE_MODIF, campagne_id, TITRE, NOM_VENDEUR, PRENOM_VENDEUR, N_CONTRAT, N_REFERENCE, VALIDATION_PRODUIT1, STATUT_PRODUIT1, VALIDATION_PRODUIT2, STATUT_PRODUIT2, VALIDATION_PRODUIT3, STATUT_PRODUIT3, EXTRANET, STRATO_NUM, STRATO_STATUT, STRATO_REMARQUE, LSR_NUM, LSR_STATUT, LSR_REMARQUE, PRESSE_NUM, PRESSE_STATUT, PRESSE_REMARQUE, ENI_NUM, ENI_STATUT, ENI_REMARQUE, SERENITY_NUM, SERENITY_STATUT, SERENITY_REMARQUE, PROTEC_ALLIANCE_NUM, PROTEC_ALLIANCE_STATUT, PROTEC_ALLIANCE_REMARQUE, WEKIWI_NUM, WEKIWI_STATUT, WEKIWI_REMARQUE, TYPE_OFFRE, TELEPHONE_VENDEUR, CALL_ID FROM clients WHERE campagne_id=?"""
    params = [campagne_id]
    if recherche:
        sql += " AND (NOM_CLIENT LIKE ? OR PRENOM_CLIENT LIKE ? OR TELEPHONE LIKE ?)"
        r = f"%{recherche}%"
        params += [r, r, r]
    if statut:
        sql += " AND STATUT=?"
        params.append(statut)
    if agent:
        sql += " AND AGENT=?"
        params.append(agent)
    if type_offre:
        sql += " AND TYPE_OFFRE=?"
        params.append(type_offre)
    if date_debut:
        sql += " AND DATE_SIGNATURE >= ?"
        params.append(date_debut)
    if date_fin:
        sql += " AND DATE_SIGNATURE <= ?"
        params.append(date_fin)

    sql_pagination = sql + " LIMIT ? OFFSET ?"
    params_pagination = params + [par_page, offset]
    c.execute(sql_pagination, params_pagination)
    clients = c.fetchall()

    # Compteurs
    base_sql = "SELECT COUNT(*) FROM clients WHERE campagne_id = ?"
    params_count = [campagne_id]
    if recherche:
        base_sql += " AND (NOM_CLIENT LIKE ? OR PRENOM_CLIENT LIKE ? OR TELEPHONE LIKE ?)"
        r = f"%{recherche}%"
        params_count += [r, r, r]
    if statut:
        base_sql += " AND STATUT=?"
        params_count.append(statut)
    if agent:
        base_sql += " AND AGENT=?"
        params_count.append(agent)
    if type_offre:
        base_sql += " AND TYPE_OFFRE=?"
        params_count.append(type_offre)
    if date_debut:
        base_sql += " AND DATE_SIGNATURE >= ?"
        params_count.append(date_debut)
    if date_fin:
        base_sql += " AND DATE_SIGNATURE <= ?"
        params_count.append(date_fin)

    def get_count_for_statut(statut_value):
        sql_count = base_sql + " AND STATUT=?"
        c.execute(sql_count, params_count + [statut_value])
        result = c.fetchone()
        return result[0] if result and result[0] is not None else 0

    count_valide = get_count_for_statut('valide')
    count_non_valide = get_count_for_statut('non valide')

    # Total pour pagination
    count_sql = "SELECT COUNT(*) FROM clients WHERE campagne_id = ?"
    count_params = [campagne_id]
    if recherche:
        count_sql += " AND (NOM_CLIENT LIKE ? OR PRENOM_CLIENT LIKE ? OR TELEPHONE LIKE ?)"
        r = f"%{recherche}%"
        count_params += [r, r, r]
    if statut:
        count_sql += " AND STATUT=?"
        count_params.append(statut)
    if agent:
        count_sql += " AND AGENT=?"
        count_params.append(agent)
    if type_offre:
        count_sql += " AND TYPE_OFFRE=?"
        count_params.append(type_offre)
    if date_debut:
        count_sql += " AND DATE_SIGNATURE >= ?"
        count_params.append(date_debut)
    if date_fin:
        count_sql += " AND DATE_SIGNATURE <= ?"
        count_params.append(date_fin)

    c.execute(count_sql, count_params)
    total_clients = c.fetchone()[0]
    total_pages = (total_clients + par_page - 1) // par_page

    conn.close()

    class Pagination:
        def __init__(self, page, total_pages):
            self.page = page
            self.total_pages = total_pages
        @property
        def has_prev(self):
            return self.page > 1
        @property
        def has_next(self):
            return self.page < self.total_pages
        @property
        def prev_num(self):
            return self.page - 1
        @property
        def next_num(self):
            return self.page + 1
        def iter_pages(self):
            left = max(1, self.page - 2)
            right = min(self.total_pages, self.page + 2)
            return range(left, right + 1)

    pagination = Pagination(page, total_pages)

    return render_template(
        'dashboard.html',
        clients=clients,
        agents=agents,
        count_valide=count_valide,
        count_non_valide=count_non_valide,
        auj=auj,
        agent_photo=agent_photo,
        pagination=pagination,
        type_offre=type_offre
    )

@app.route('/dashboard_valandre')
def dashboard_valandre():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    if session.get('agent_role') not in ['admin', 'superviseur'] and session.get('campagne_id') != 2:
        flash("Accès interdit.", "danger")
        return redirect(url_for('index'))

    # Imports locaux pour éviter les NameError
    import math, sqlite3, unicodedata
    from datetime import datetime, date
    import pandas as pd

    # ---- Par défaut: afficher uniquement aujourd'hui (si aucun filtre date n'est fourni) ----
    if 'date_debut' not in request.args and 'date_fin' not in request.args:
        today_str = date.today().strftime("%Y-%m-%d")
        return redirect(url_for('dashboard_valandre', date_debut=today_str, date_fin=today_str))

    # ---- pagination simple ----
    class SimplePagination:
        def __init__(self, page, per_page, total):
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = max(1, math.ceil(total / per_page))
        @property
        def has_prev(self): return self.page > 1
        @property
        def has_next(self): return self.page < self.pages
        @property
        def prev_num(self): return self.page - 1
        @property
        def next_num(self): return self.page + 1
        def iter_pages(self, left_edge=2, left_current=2, right_current=2, right_edge=2):
            last = 0
            for num in range(1, self.pages + 1):
                if (num <= left_edge or
                    (num >= self.page - left_current and num <= self.page + right_current) or
                    num > self.pages - right_edge):
                    if last + 1 != num:
                        yield None
                    yield num
                    last = num

    # ---- normalisation statuts produit -> "valide" / "non valide" / autre ----
    def norm_statut(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        if s in {"valide", "validee", "ok", "oui", "validate", "accepted"}:
            return "valide"
        if s in {"non valide", "refuse", "refusee", "refus", "ko", "non", "rejete", "rejet"}:
            return "non valide"
        return s  # "", "en cours", etc.

    PRODUITS = ["STRATO","LSR","PRESSE","ENI","SERENITY","PROTEC_ALLIANCE","WEKIWI"]

    # ---- lecture base : VALANDRE depuis 2 sources (clients + clients_valandre) ----
    conn = sqlite3.connect(DB_NAME)
    try:
        # 1) Table 'clients' (ancienne logique) filtrée sur campagne_id = 2
        try:
            df_clients = pd.read_sql_query(
                "SELECT * FROM clients WHERE campagne_id = 2 ORDER BY id DESC", conn
            )
        except Exception:
            df_clients = pd.DataFrame()

        # 2) Table dédiée 'clients_valandre' (si elle existe)
        try:
            df_valandre = pd.read_sql_query(
                "SELECT * FROM clients_valandre ORDER BY id DESC", conn
            )
        except Exception:
            df_valandre = pd.DataFrame()

        # 2.b) Harmonisation minimale des colonnes clés attendues
        if not df_valandre.empty:
            for col in ["DATE_SIGNATURE", "NOM_CLIENT", "PRENOM_CLIENT", "TELEPHONE", "AGENT"]:
                if col not in df_valandre.columns:
                    df_valandre[col] = ""
            if "campagne_id" not in df_valandre.columns:
                df_valandre["campagne_id"] = 2

        # 3) Fusion des deux sources
        if (df_clients is None or df_clients.empty) and (df_valandre is None or df_valandre.empty):
            df = pd.DataFrame()
        elif df_clients is None or df_clients.empty:
            df = df_valandre.copy()
        elif df_valandre is None or df_valandre.empty:
            df = df_clients.copy()
        else:
            df = pd.concat([df_clients, df_valandre], ignore_index=True, sort=False)

    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df is None or df.empty:
        clients = []
        agents = []
        count_valide = 0
        count_refuse = 0
        pagination = SimplePagination(1, 25, 0)
        return render_template(
            'dashboard_valandre.html',
            clients=clients, agents=agents,
            count_valide=count_valide, count_refuse=count_refuse,
            pagination=pagination,
            agent_photo=session.get("agent_photo")
        )

    # ---- prépa colonnes ----
    df = df.fillna("")
    # Parse DATE_SIGNATURE (coerce => NaT si format invalide)
    sig_dt = pd.to_datetime(df.get("DATE_SIGNATURE", ""), errors="coerce", infer_datetime_format=True)
    df["__sig_date__"] = sig_dt.dt.date  # type: datetime.date

    # Normalise les statuts par produit
    for p in PRODUITS:
        col = f"{p}_STATUT"
        if col in df.columns:
            df[col] = df[col].apply(norm_statut)

    # statut global de la ligne
    statut_cols = [c for c in df.columns if c.endswith("_STATUT")]
    any_valid  = df[statut_cols].eq("valide").any(axis=1) if statut_cols else False
    any_refuse = df[statut_cols].eq("non valide").any(axis=1) if statut_cols else False
    df["row_status"] = ""
    df.loc[any_valid, "row_status"] = "valide"
    df.loc[~any_valid & any_refuse, "row_status"] = "non valide"

    # ---- filtres GET ----
    recherche   = (request.args.get("recherche", "") or "").strip().lower()
    agent_f     = (request.args.get("agent", "") or "").strip()
    statut_f    = (request.args.get("statut", "") or "").strip().lower()
    date_debut  = (request.args.get("date_debut", "") or "").strip()
    date_fin    = (request.args.get("date_fin", "") or "").strip()

    mask = pd.Series(True, index=df.index)

    if recherche:
        def contains(col):
            return df[col].astype(str).str.lower().str.contains(recherche, na=False)
        mask &= (contains("NOM_CLIENT") | contains("PRENOM_CLIENT") |
                 df["TELEPHONE"].astype(str).str.contains(recherche, na=False))

    if agent_f:
        mask &= (df["AGENT"].astype(str) == agent_f)

    if statut_f:
        mask &= (df["row_status"].astype(str).str.lower() == statut_f)

    # Filtre dates inclusif
    start_date = None
    end_date = None
    if date_debut:
        try:
            start_date = pd.to_datetime(date_debut, errors="raise").date()
        except Exception:
            start_date = None
    if date_fin:
        try:
            end_date = pd.to_datetime(date_fin, errors="raise").date()
        except Exception:
            end_date = None
    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date
    if start_date:
        mask &= (df["__sig_date__"] >= start_date)
    if end_date:
        mask &= (df["__sig_date__"] <= end_date)

    df = df[mask].copy()

    # ---- compteurs du jour ----
    today_d = date.today()
    today_df = df[df["__sig_date__"] == today_d]
    count_valide = int((today_df["row_status"] == "valide").sum())
    count_refuse = int((today_df["row_status"] == "non valide").sum())

    # ---- liste agents ----
    agents = sorted(set(df["AGENT"].astype(str))) if "AGENT" in df.columns else []

    # ---- pagination ----
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 25
    total = len(df)
    start = (page - 1) * per_page
    end = start + per_page
    page_df = df.iloc[start:end]
    pagination = SimplePagination(page, per_page, total)

    clients = page_df.to_dict(orient="records")

    return render_template(
        'dashboard_valandre.html',
        clients=clients,
        agents=agents,
        count_valide=count_valide,
        count_refuse=count_refuse,
        pagination=pagination,
        agent_photo=session.get("agent_photo")
    )

# ──────────────────────────────────────────────────────────────────────────────
# Formulaire Valandre
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/formulaire_valandre', methods=['GET', 'POST'])
def formulaire_valandre():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    # Accès : admin/superviseur OU campagne 2
    if session.get('agent_role') not in ['admin', 'superviseur'] and session.get('campagne_id') != 2:
        flash("Accès interdit à ce formulaire.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        # --- Récup inputs généraux ---
        date_signature = request.form.get("DATE_SIGNATURE") or datetime.now().strftime('%Y-%m-%d')
        nom_client     = (request.form.get("NOM_CLIENT") or "").strip()
        prenom_client  = (request.form.get("PRENOM_CLIENT") or "").strip()
        telephone      = (request.form.get("TELEPHONE") or "").strip()
        nom_vendeur    = (request.form.get("NOM_VENDEUR") or "").strip()
        prenom_vendeur = (request.form.get("PRENOM_VENDEUR") or "").strip()
        titre          = (request.form.get("TITRE") or "").strip()
        agent          = (request.form.get("AGENT") or "").strip()
        extranet       = (request.form.get("EXTRANET") or "").strip()

        # Validation minimale
        if not (nom_client and prenom_client and telephone and nom_vendeur and prenom_vendeur and titre):
            flash("Veuillez remplir tous les champs obligatoires.", "danger")
            return redirect(url_for("formulaire_valandre"))

        # --- Produits cochés ---
        PRODUITS = ["STRATO","LSR","PRESSE","ENI","SERENITY","PROTEC_ALLIANCE","WEKIWI"]
        produits_sel = request.form.getlist("produits[]")  # <— IMPORTANT: même nom que dans le template

        # Vérifier que pour chaque produit coché on a bien NUM + STATUT
        for p in produits_sel:
            num = (request.form.get(f"{p}_NUM") or "").strip()
            statut = (request.form.get(f"{p}_STATUT") or "").strip()
            if not num or not statut:
                flash(f"Veuillez remplir le numéro et le statut pour le produit {p.replace('_', ' ')}.", "danger")
                return redirect(url_for("formulaire_valandre"))

        # --- Préparer la table & l'insert ---
        # Table dédiée (évite de mélanger avec les clients SFR)
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients_valandre (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                DATE_SIGNATURE TEXT,
                NOM_VENDEUR TEXT, PRENOM_VENDEUR TEXT, TITRE TEXT,
                NOM_CLIENT TEXT, PRENOM_CLIENT TEXT, TELEPHONE TEXT,
                AGENT TEXT, EXTRANET TEXT,
                STRATO_NUM TEXT, STRATO_STATUT TEXT, STRATO_REMARQUE TEXT,
                LSR_NUM TEXT, LSR_STATUT TEXT, LSR_REMARQUE TEXT,
                PRESSE_NUM TEXT, PRESSE_STATUT TEXT, PRESSE_REMARQUE TEXT,
                ENI_NUM TEXT, ENI_STATUT TEXT, ENI_REMARQUE TEXT,
                SERENITY_NUM TEXT, SERENITY_STATUT TEXT, SERENITY_REMARQUE TEXT,
                PROTEC_ALLIANCE_NUM TEXT, PROTEC_ALLIANCE_STATUT TEXT, PROTEC_ALLIANCE_REMARQUE TEXT,
                WEKIWI_NUM TEXT, WEKIWI_STATUT TEXT, WEKIWI_REMARQUE TEXT,
                created_at TEXT
            )
        """)

        # Colonnes cibles dans l'ordre
        colonnes = [
            "DATE_SIGNATURE",
            "NOM_VENDEUR","PRENOM_VENDEUR","TITRE",
            "NOM_CLIENT","PRENOM_CLIENT","TELEPHONE",
            "AGENT","EXTRANET",
            "STRATO_NUM","STRATO_STATUT","STRATO_REMARQUE",
            "LSR_NUM","LSR_STATUT","LSR_REMARQUE",
            "PRESSE_NUM","PRESSE_STATUT","PRESSE_REMARQUE",
            "ENI_NUM","ENI_STATUT","ENI_REMARQUE",
            "SERENITY_NUM","SERENITY_STATUT","SERENITY_REMARQUE",
            "PROTEC_ALLIANCE_NUM","PROTEC_ALLIANCE_STATUT","PROTEC_ALLIANCE_REMARQUE",
            "WEKIWI_NUM","WEKIWI_STATUT","WEKIWI_REMARQUE",
            "created_at"
        ]

        # Valeurs par défaut
        rec = {c: "" for c in colonnes}
        rec["DATE_SIGNATURE"] = date_signature
        rec["NOM_VENDEUR"]    = nom_vendeur
        rec["PRENOM_VENDEUR"] = prenom_vendeur
        rec["TITRE"]          = titre
        rec["NOM_CLIENT"]     = nom_client
        rec["PRENOM_CLIENT"]  = prenom_client
        rec["TELEPHONE"]      = telephone
        rec["AGENT"]          = agent
        rec["EXTRANET"]       = extranet
        rec["created_at"]     = datetime.now().isoformat(timespec="seconds")

        # Remplir les champs des produits cochés uniquement
        for p in PRODUITS:
            rec[f"{p}_NUM"]      = (request.form.get(f"{p}_NUM") or "").strip() if p in produits_sel else ""
            rec[f"{p}_STATUT"]   = (request.form.get(f"{p}_STATUT") or "").strip() if p in produits_sel else ""
            rec[f"{p}_REMARQUE"] = (request.form.get(f"{p}_REMARQUE") or "").strip() if p in produits_sel else ""

        placeholders = ",".join("?" for _ in colonnes)
        sql = f"INSERT INTO clients_valandre ({','.join(colonnes)}) VALUES ({placeholders})"
        cur.execute(sql, tuple(rec[c] for c in colonnes))
        conn.commit()
        conn.close()

        flash("Client enregistré avec succès.", "success")
        return redirect(url_for("dashboard_valandre"))

    # GET → afficher formulaire
    agents = get_agents()
    date_auj = datetime.now().strftime('%Y-%m-%d')
    return render_template(
        'formulaire_valandre.html',
        agents=agents,
        agent_nom=session['agent_nom'],
        agent_role=session['agent_role'],
        date_auj=date_auj
    )

# ──────────────────────────────────────────────────────────────────────────────
# Paramètres / Agents
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/parametres', methods=['GET', 'POST'])
def parametres():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('index'))
    roles = ["agent", "admin", "superviseur"]
    if request.method == 'POST':
        nom = request.form['NOM']
        login = request.form['LOGIN']
        mdp = request.form['MDP']
        role = request.form['ROLE']
        tv  = (request.form.get('TV') or '').strip()   # <-- AJOUT

        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                if not allowed_file(filename):
                    flash("Format de fichier non autorisé. Utilise JPG, JPEG ou PNG.", "danger")
                    return redirect(request.url)
                if not file_size_okay(file):
                    flash("Le fichier est trop lourd. Taille max : 5 Mo.", "danger")
                    return redirect(request.url)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                photo_filename = filename

        hashed_mdp = bcrypt.hashpw(mdp.encode('utf-8'), bcrypt.gensalt())
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            # ⚠️ colonne TV en majuscules, et 6 valeurs pour 6 colonnes
            c.execute(
                "INSERT INTO agents (NOM, LOGIN, MDP, ROLE, photo, TV) VALUES (?, ?, ?, ?, ?, ?)",
                (nom, login, hashed_mdp, role, photo_filename, tv)
            )
            conn.commit()
            conn.close()
            flash("Nouvel agent créé avec succès !", "success")
        except sqlite3.IntegrityError:
            flash("Nom ou login déjà utilisé !", "danger")
        return redirect('/parametres')


    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
    SELECT a.id, a.NOM, a.LOGIN, a.ROLE, c.nom, COALESCE(a.TV,'')
    FROM agents a
    LEFT JOIN campagnes c ON a.campagne_id = c.id
    """)
    agents = c.fetchall()
    conn.close()
    campagnes = get_campagnes()
    return render_template('parametres.html', agents=agents, roles=roles, campagnes=campagnes)

# ──────────────────────────────────────────────────────────────
# ROUTES PROFIL / MODIFIER / SUPPRIMER AGENT (avec PAYS_CODE)
# ──────────────────────────────────────────────────────────────
import os
from flask import request, session, redirect, url_for, flash, render_template
from werkzeug.utils import secure_filename
import bcrypt

# Hypothèses : ces variables existent déjà dans ton app.py
# DB_NAME : chemin de ta base SQLite (ex: "data/app.db")
# app : instance Flask
# COUNTRIES : liste de pays (depuis currency_utils.load_countries())
# COUNTRY_NAME_BY_CODE : mapping {'FR': 'France', ...}

def _is_valid_country_code(code: str) -> bool:
    if not code:
        return False
    code = code.strip().upper()
    return any(c["code"] == code for c in COUNTRIES)

@app.route('/profil', methods=['GET', 'POST'])
def profil_agent():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    agent_nom = session['agent_nom']
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # On récupère l'agent par NOM (tel que stocké en session)
    c.execute("SELECT id, NOM, photo, COALESCE(PAYS_CODE, '') FROM agents WHERE NOM = ?", (agent_nom,))
    agent = c.fetchone()
    if not agent:
        conn.close()
        flash("Agent introuvable.", "danger")
        return redirect(url_for('dashboard'))

    agent_id, agent_nom_db, agent_photo, agent_pays_code = agent
    photo_url = url_for('static', filename='uploads/' + agent_photo) if agent_photo else url_for('static', filename='img/avatar.png')

    # Stats perso
    c.execute("SELECT COUNT(*) FROM clients WHERE AGENT = ? AND STATUT = 'valide'", (agent_nom,))
    clients_valides = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM clients WHERE AGENT = ?", (agent_nom,))
    total_clients = c.fetchone()[0] or 0

    # Classement
    c.execute("""
        SELECT AGENT, COUNT(*) as nb_valides
        FROM clients
        WHERE STATUT = 'valide'
        GROUP BY AGENT
        ORDER BY nb_valides DESC
    """)
    classement = c.fetchall()
    position = "-"
    for idx, (nom, nb) in enumerate(classement, 1):
        if nom == agent_nom:
            position = idx
            break

    # Upload photo
    if request.method == 'POST':
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '':
                filename = secure_filename(f"{agent_id}_{file.filename}")
                upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                # Crée le dossier si besoin
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(upload_path)
                c.execute("UPDATE agents SET photo=? WHERE id=?", (filename, agent_id))
                conn.commit()
                flash("Photo de profil mise à jour avec succès !", "success")
                conn.close()
                return redirect(url_for('profil_agent'))
        flash("Aucune photo sélectionnée.", "warning")

    conn.close()
    return render_template(
        'profil_agent.html',
        agent_nom=agent_nom,
        photo_url=photo_url,
        clients_valides=clients_valides,
        total_clients=total_clients,
        position=position,
        agent_pays=COUNTRY_NAME_BY_CODE.get(agent_pays_code, "-"),
        countries=COUNTRIES,                  # pour affichage éventuel
        country_names=COUNTRY_NAME_BY_CODE    # pour mappage code -> nom
    )

@app.route('/modifier_agent/<int:agent_id>', methods=['GET', 'POST'])
def modifier_agent(agent_id):
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('index'))

    roles = ["agent", "admin", "superviseur"]
    campagnes = get_campagnes()  # doit exister dans ton app
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # On récupère aussi PAYS_CODE pour le pré-remplissage
    c.execute("SELECT id, NOM, LOGIN, ROLE, campagne_id, COALESCE(PAYS_CODE,'') FROM agents WHERE id = ?", (agent_id,))
    agent = c.fetchone()
    if not agent:
        conn.close()
        flash("Agent introuvable.", "danger")
        return redirect(url_for('parametres'))

    if request.method == 'POST':
        # Champs du formulaire
        nom = request.form.get('NOM', '').strip()
        login = request.form.get('LOGIN', '').strip()
        mdp = request.form.get('MDP', '')
        role = request.form.get('ROLE', 'agent').strip()

        # CAMPAGNE_ID : si vide, on conserve la valeur actuelle
        campagne_id_str = request.form.get('CAMPAGNE_ID', '').strip()
        if campagne_id_str == '':
            c.execute("SELECT campagne_id FROM agents WHERE id=?", (agent_id,))
            row = c.fetchone()
            campagne_id = row[0] if row else None
        else:
            try:
                campagne_id = int(campagne_id_str)
            except ValueError:
                campagne_id = None

        # PAYS_CODE (NOUVEAU) : requis, validé contre COUNTRIES
        pays_code = (request.form.get('PAYS_CODE', '') or '').strip().upper()
        if not _is_valid_country_code(pays_code):
            conn.close()
            flash("Code pays invalide. Merci de sélectionner un pays dans la liste.", "warning")
            return redirect(url_for('modifier_agent', agent_id=agent_id))

        # Mises à jour
        if mdp.strip():
            hashed_mdp = bcrypt.hashpw(mdp.encode('utf-8'), bcrypt.gensalt())
            c.execute(
                "UPDATE agents SET NOM=?, LOGIN=?, MDP=?, ROLE=?, campagne_id=?, PAYS_CODE=? WHERE id=?",
                (nom, login, hashed_mdp, role, campagne_id, pays_code, agent_id)
            )
        else:
            c.execute(
                "UPDATE agents SET NOM=?, LOGIN=?, ROLE=?, campagne_id=?, PAYS_CODE=? WHERE id=?",
                (nom, login, role, campagne_id, pays_code, agent_id)
            )

        conn.commit()
        conn.close()
        flash("Agent modifié avec succès.", "success")
        return redirect(url_for('parametres'))

    # GET : on affiche la page avec les infos actuelles
    conn.close()
    return render_template(
        'modifier_agent.html',
        agent=agent,              # tuple: (id, NOM, LOGIN, ROLE, campagne_id, PAYS_CODE)
        roles=roles,
        campagnes=campagnes,
        countries=COUNTRIES,                 # pour alimenter le <select>
        country_names=COUNTRY_NAME_BY_CODE   # utile si tu veux montrer le nom entier
    )

@app.route('/supprimer_agent/<int:agent_id>', methods=['POST'])
def supprimer_agent(agent_id):
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('index'))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM agents")
    count = c.fetchone()[0]
    if count <= 1:
        flash("Impossible de supprimer le dernier agent.", "danger")
    else:
        c.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        conn.commit()
        flash("Agent supprimé avec succès.", "success")
    conn.close()
    return redirect(url_for('parametres'))

@app.route('/supprimer_client/<int:client_id>', methods=['POST'])
def supprimer_client(client_id):
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # --- CONTRÔLE D'AUTORISATION ---
    role = session.get('agent_role')
    agent_session = session.get('agent_nom')

    c.execute("SELECT AGENT FROM clients WHERE id=?", (client_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        flash("Client introuvable.", "danger")
        return redirect(url_for('dashboard'))

    owner = row[0]

    if role not in ("admin", "superviseur") and owner != agent_session:
        conn.close()
        flash("Accès refusé : vous ne pouvez supprimer que vos propres fiches.", "danger")
        return redirect(url_for('dashboard'))

    c.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
    conn.close()
    flash("Client supprimé.", "info")
    return redirect(url_for('dashboard'))
@app.route('/modifier_client/<int:client_id>', methods=['GET', 'POST'])
def modifier_client(client_id):
    """
    Modifie une fiche client :
      - ?src=valandre => force la table clients_valandre
      - sinon, on vérifie si l'id existe dans clients_valandre, à défaut on utilise clients.
    Met à jour uniquement les colonnes réellement présentes (ex: MODIFIE_PAR/DATE_MODIF optionnelles).
    """
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    def _norm_statut(x: str) -> str:
        """Normalise les statuts produits pour la DB."""
        s = (x or "").strip().lower()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        if s in {"valide", "validee", "ok", "oui", "accepted", "validate"}:
            return "valide"
        if s in {"non valide", "refuse", "refusee", "ko", "non", "refus", "rejete", "rejet"}:
            return "non valide"
        return s

    def table_columns(cur, table_name: str) -> set[str]:
        cur.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cur.fetchall()}

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ---------- Sélection de la table ----------
    src = (request.args.get('src') or "").lower()
    if src == "valandre":
        table = "clients_valandre"
    else:
        c.execute("SELECT 1 FROM clients_valandre WHERE id = ?", (client_id,))
        table = "clients_valandre" if c.fetchone() else "clients"

    cols = table_columns(c, table)

    # ---------- Contrôle d'accès ----------
    if "AGENT" not in cols:
        conn.close()
        flash("Schéma invalide : colonne AGENT manquante.", "danger")
        return redirect(url_for('dashboard_valandre' if table == 'clients_valandre' else 'dashboard'))

    c.execute(f"SELECT AGENT FROM {table} WHERE id = ?", (client_id,))
    r = c.fetchone()
    if not r:
        conn.close()
        flash("Client introuvable.", "danger")
        return redirect(url_for('dashboard_valandre' if table == 'clients_valandre' else 'dashboard'))

    owner = (r["AGENT"] or "")
    role = session.get('agent_role')
    agent_session = session.get('agent_nom')

    if role not in ("admin", "superviseur") and owner != agent_session:
        conn.close()
        flash("Accès refusé : vous ne pouvez modifier que vos propres fiches.", "danger")
        return redirect(url_for('dashboard_valandre' if table == 'clients_valandre' else 'dashboard'))

    # ---------- POST : mise à jour ----------
    if request.method == 'POST':
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if table == "clients_valandre":
            PRODUITS = ["STRATO","LSR","PRESSE","ENI","SERENITY","PROTEC_ALLIANCE","WEKIWI"]

            champs_base = [
                'DATE_SIGNATURE','NOM_VENDEUR','PRENOM_VENDEUR','TITRE',
                'NOM_CLIENT','PRENOM_CLIENT','TELEPHONE'
            ]
            champs_prod = []
            for p in PRODUITS:
                champs_prod += [f"{p}_NUM", f"{p}_STATUT", f"{p}_REMARQUE"]
            champs_fin = ['EXTRANET','AGENT']

            # On ne garde que les colonnes présentes dans la table
            champs = [x for x in (champs_base + champs_prod + champs_fin) if x in cols]

            # Anciennes valeurs
            c.execute(f"SELECT {', '.join(champs)} FROM {table} WHERE id = ?", (client_id,))
            ancien = c.fetchone()

            # Nouvelles valeurs (normalisation des *_STATUT)
            valeurs = {}
            for ch in champs:
                if ch.endswith("_STATUT"):
                    valeurs[ch] = _norm_statut(request.form.get(ch, ''))
                else:
                    valeurs[ch] = request.form.get(ch, '')

            # Historique (si la table existe)
            existing_tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if ancien is not None and "historique_clients" in existing_tables:
                for ch in champs:
                    old_val = "" if ancien[ch] is None else str(ancien[ch])
                    new_val = "" if valeurs[ch] is None else str(valeurs[ch])
                    if old_val != new_val:
                        c.execute("""
                            INSERT INTO historique_clients (client_id, date_modif, agent, champ_modifie, ancienne_valeur, nouvelle_valeur)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (client_id, now_str, session['agent_nom'], ch, old_val, new_val))

            # UPDATE dynamique (ajoute MODIFIE_PAR / DATE_MODIF si présentes)
            set_parts = [f"{ch}=?" for ch in champs]
            params = [valeurs[ch] for ch in champs]
            if "MODIFIE_PAR" in cols:
                set_parts.append("MODIFIE_PAR=?")
                params.append(session['agent_nom'])
            if "DATE_MODIF" in cols:
                set_parts.append("DATE_MODIF=?")
                params.append(now_str)

            params.append(client_id)
            c.execute(f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = ?", params)
            conn.commit()
            conn.close()

            try:
                notifier_nouveau_client(request.form.get('NOM_CLIENT',''))
            except Exception:
                pass

            flash("Client VALANDRE modifié avec succès.", "success")
            return redirect(url_for('dashboard_valandre'))

        # ----- Table 'clients' (SFR/Autres) -----
        champs_all = [
            'DATE_SIGNATURE','CIVILITE_CLIENT','NOM_CLIENT','PRENOM_CLIENT','TELEPHONE',
            'STATUT','AGENT','DEUXIEME_ADRESSE','TROISIEME_ADRESSE'
        ]
        champs = [x for x in champs_all if x in cols]

        c.execute(f"SELECT {', '.join(champs)} FROM {table} WHERE id = ?", (client_id,))
        ancien = c.fetchone()

        valeurs = {ch: request.form.get(ch, '') for ch in champs}

        existing_tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if ancien is not None and "historique_clients" in existing_tables:
            for ch in champs:
                old_val = "" if ancien[ch] is None else str(ancien[ch])
                new_val = "" if valeurs[ch] is None else str(valeurs[ch])
                if old_val != new_val:
                    c.execute("""
                        INSERT INTO historique_clients (client_id, date_modif, agent, champ_modifie, ancienne_valeur, nouvelle_valeur)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (client_id, now_str, session['agent_nom'], ch, old_val, new_val))

        set_parts = [f"{ch}=?" for ch in champs]
        params = [valeurs[ch] for ch in champs]
        if "MODIFIE_PAR" in cols:
            set_parts.append("MODIFIE_PAR=?")
            params.append(session['agent_nom'])
        if "DATE_MODIF" in cols:
            set_parts.append("DATE_MODIF=?")
            params.append(now_str)

        params.append(client_id)
        c.execute(f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = ?", params)
        conn.commit()
        conn.close()

        try:
            notifier_nouveau_client(request.form.get('NOM_CLIENT',''))
        except Exception:
            pass

        flash("Client modifié avec succès.", "success")
        return redirect(url_for('dashboard_valandre' if table == 'clients_valandre' else 'dashboard'))

    # ---------- GET : affichage formulaire ----------
    if table == "clients_valandre":
        c.execute(f"SELECT * FROM {table} WHERE id = ?", (client_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            flash("Client introuvable.", "danger")
            return redirect(url_for('dashboard_valandre'))

        client = dict(row)  # dict pour le template Valandre
        agents = get_agents()
        conn.close()
        return render_template('modifier_client_valandre.html', client=client, agents=agents)

    else:
        c.execute(f"SELECT * FROM {table} WHERE id = ?", (client_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            flash("Client introuvable.", "danger")
            return redirect(url_for('dashboard'))

        client = row  # tuple/Row pour le template legacy
        agents = get_agents()
        conn.close()
        return render_template('modifier_client.html', client=client, agents=agents)



# ──────────────────────────────────────────────────────────────────────────────
# Historique client
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/historique_client/<int:client_id>')
def historique_client(client_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT date_modif, agent, champ_modifie, ancienne_valeur, nouvelle_valeur
        FROM historique_clients
        WHERE client_id=?
        ORDER BY date_modif DESC
    """, (client_id,))
    historique = c.fetchall()
    conn.close()
    return render_template('historique_client.html', historique=historique, client_id=client_id)

# ──────────────────────────────────────────────────────────────────────────────
# Exports
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/export_excel_valandre')
def export_excel_valandre():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    # Imports locaux (pour éviter NameError si appel direct)
    import os, sqlite3, tempfile, unicodedata
    from datetime import datetime
    import pandas as pd
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Alignment
    from flask import send_file

    def clean_date(dt):
        if not dt:
            return ""
        dt = dt.strip()
        if "-" in dt:
            return dt
        if "/" in dt:
            try:
                return datetime.strptime(dt, "%d/%m/%Y").strftime("%Y-%m-%d")
            except Exception:
                return dt
        return dt

    def norm_statut(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        if s in {"valide", "validee", "ok", "oui", "validate", "accepted"}:
            return "valide"
        if s in {"non valide", "refuse", "refusee", "refus", "ko", "non", "rejete", "rejet"}:
            return "non valide"
        return s  # "", "en cours", etc.

    # --- Récup paramètres URL
    date_debut = clean_date((request.args.get('date_debut') or '').strip())
    date_fin   = clean_date((request.args.get('date_fin') or '').strip())
    telephone  = (request.args.get('telephone') or '').strip()
    agent      = (request.args.get('agent') or '').strip()
    statut_f   = (request.args.get('statut') or '').strip().lower()  # "valide" / "non valide" (optionnel)

    produits = ["STRATO", "LSR", "PRESSE", "ENI", "SERENITY", "PROTEC_ALLIANCE", "WEKIWI"]

    # Colonnes exportées (même ordre que ton fichier d'origine)
    select_cols = ["DATE_SIGNATURE", "NOM_VENDEUR", "PRENOM_VENDEUR", "TITRE", "NOM_CLIENT", "PRENOM_CLIENT", "TELEPHONE"]
    for prod in produits:
        select_cols += [f"{prod}_NUM", f"{prod}_STATUT", f"{prod}_REMARQUE"]
    select_cols += ["EXTRANET", "AGENT"]

    # --- Campagne_id de VALANDRE (fallback=2)
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM campagnes WHERE nom = 'VALANDRE'")
        row = c.fetchone()
        campagne_id = row[0] if row else 2

        # --- Lecture des deux sources
        try:
            df_clients = pd.read_sql_query(
                "SELECT * FROM clients WHERE campagne_id = ?",
                conn, params=[campagne_id]
            )
        except Exception:
            df_clients = pd.DataFrame()

        try:
            df_valandre = pd.read_sql_query("SELECT * FROM clients_valandre", conn)
        except Exception:
            df_valandre = pd.DataFrame()
    finally:
        conn.close()

    # --- Harmonisation minimale des colonnes clés pour clients_valandre
    if not df_valandre.empty:
        for col in ["DATE_SIGNATURE", "NOM_VENDEUR", "PRENOM_VENDEUR", "TITRE",
                    "NOM_CLIENT", "PRENOM_CLIENT", "TELEPHONE", "EXTRANET", "AGENT"]:
            if col not in df_valandre.columns:
                df_valandre[col] = ""
        if "campagne_id" not in df_valandre.columns:
            df_valandre["campagne_id"] = campagne_id
        # S'assurer que les colonnes produits existent
        for prod in produits:
            for suf in ["NUM", "STATUT", "REMARQUE"]:
                col = f"{prod}_{suf}"
                if col not in df_valandre.columns:
                    df_valandre[col] = ""

    # --- Fusion des deux sources
    if (df_clients is None or df_clients.empty) and (df_valandre is None or df_valandre.empty):
        df = pd.DataFrame(columns=select_cols)
    elif df_clients is None or df_clients.empty:
        df = df_valandre.copy()
    elif df_valandre is None or df_valandre.empty:
        df = df_clients.copy()
    else:
        df = pd.concat([df_clients, df_valandre], ignore_index=True, sort=False)

    # Si aucune donnée, on génère quand même un fichier avec les en-têtes
    if df.empty:
        wb = Workbook()
        ws = wb.active
        # En-tête double ligne
        header1 = ["DATE DE SIGNATURE", "NOM VENDEUR", "PRENOM VENDEUR", "TITRE", "NOM CLIENT", "PRENOM CLIENT", "TÉLÉPHONE"]
        for prod in produits:
            header1.extend([f"VALIDATION {prod}"] * 3)
        header1.extend(["EXTRANET", "AGENT"])
        ws.append(header1)
        header2 = ["", "", "", "", "", "", ""]
        for _ in produits:
            header2.extend(["N° CONTRAT/RÉF", "STATUT", "REMARQUE"])
        header2.extend(["", ""])
        ws.append(header2)
        # Fusions colonnes
        col = 1
        for _ in range(7):
            ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
            col += 1
        for _ in produits:
            ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col+2)
            col += 3
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col); col += 1
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)

        for cell in ws["1:1"]:
            cell.alignment = Alignment(horizontal='center', vertical='center')
        for cell in ws["2:2"]:
            cell.alignment = Alignment(horizontal='center', vertical='center')

        # Autosize
        for idx in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(idx)].width = 18

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            wb.save(tmp.name)
            tmp_path = tmp.name

        resp = send_file(tmp_path, as_attachment=True, download_name="export_valandre.xlsx")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return resp

    # --- Nettoyage / normalisation
    df = df.fillna("")
    # Normaliser les statuts produits
    for prod in produits:
        col_stat = f"{prod}_STATUT"
        if col_stat in df.columns:
            df[col_stat] = df[col_stat].apply(norm_statut)

    # Statut global (comme dans le dashboard)
    statut_cols = [c for c in df.columns if c.endswith("_STATUT")]
    if statut_cols:
        any_valid = df[statut_cols].eq("valide").any(axis=1)
        any_refus = df[statut_cols].eq("non valide").any(axis=1)
        df["__row_status__"] = ""
        df.loc[any_valid, "__row_status__"] = "valide"
        df.loc[~any_valid & any_refus, "__row_status__"] = "non valide"
    else:
        df["__row_status__"] = ""

    # --- Filtres
    # Dates (on convertit en datetime puis on filtre)
    dt = pd.to_datetime(df["DATE_SIGNATURE"], errors="coerce")
    if date_debut:
        try:
            d0 = pd.to_datetime(date_debut)
            dt_mask = (dt >= d0)
            df = df[dt_mask.fillna(False)]
        except Exception:
            pass
    if date_fin:
        try:
            d1 = pd.to_datetime(date_fin) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            dt_mask = (pd.to_datetime(df["DATE_SIGNATURE"], errors="coerce") <= d1)
            df = df[dt_mask.fillna(False)]
        except Exception:
            pass

    # Téléphone (LIKE %xxx%)
    if telephone:
        df = df[df["TELEPHONE"].astype(str).str.contains(telephone, na=False)]

    # Agent (égalité stricte)
    if agent:
        df = df[df["AGENT"].astype(str) == agent]

    # Statut global ("valide" / "non valide")
    if statut_f in {"valide", "non valide"}:
        df = df[df["__row_status__"] == statut_f]

    # --- Tri (DATE_SIGNATURE décroissant si possible)
    try:
        df["__dt__"] = pd.to_datetime(df["DATE_SIGNATURE"], errors="coerce")
        df = df.sort_values("__dt__", ascending=False)
    except Exception:
        pass

    # --- Construction du classeur Excel
    wb = Workbook()
    ws = wb.active

    # Ligne d'en-têtes 1
    header1 = ["DATE DE SIGNATURE", "NOM VENDEUR", "PRENOM VENDEUR", "TITRE", "NOM CLIENT", "PRENOM CLIENT", "TÉLÉPHONE"]
    for prod in produits:
        header1.extend([f"VALIDATION {prod}"] * 3)
    header1.extend(["EXTRANET", "AGENT"])
    ws.append(header1)

    # Ligne d'en-têtes 2
    header2 = ["", "", "", "", "", "", ""]
    for _ in produits:
        header2.extend(["N° CONTRAT/RÉF", "STATUT", "REMARQUE"])
    header2.extend(["", ""])
    ws.append(header2)

    # Fusions (comme ton fichier)
    col = 1
    for _ in range(7):
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
        col += 1
    for _ in produits:
        ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col+2)
        col += 3
    ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col); col += 1
    ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)

    # Lignes de données (respecter l'ordre select_cols)
    # S'assurer que toutes les colonnes existent
    for colname in select_cols:
        if colname not in df.columns:
            df[colname] = ""

    for _, r in df[select_cols].iterrows():
        ws.append([r.get(c, "") for c in select_cols])

    # Alignements entêtes
    for cell in ws["1:1"]:
        cell.alignment = Alignment(horizontal='center', vertical='center')
    for cell in ws["2:2"]:
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # Autosize colonnes
    for idx, column_cells in enumerate(ws.columns, 1):
        max_length = 0
        for cell in column_cells:
            if cell.value is not None:
                try:
                    max_length = max(max_length, len(str(cell.value)))
                except Exception:
                    pass
        ws.column_dimensions[get_column_letter(idx)].width = min(max_length + 2, 60)

    # --- Envoi du fichier
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
        wb.save(tmp.name)
        tmp_path = tmp.name

    response = send_file(tmp_path, as_attachment=True, download_name="export_valandre.xlsx")
    try:
        os.remove(tmp_path)
    except Exception:
        pass
    return response



# ──────────────────────────────────────────────────────────────────────────────
# Export SFR
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/export_excel_sfr')
def export_excel_sfr():
    if 'agent_nom' not in session:
        return redirect(url_for('login'))

    date_debut = (request.args.get('date_debut', '') or '').strip()
    date_fin   = (request.args.get('date_fin', '') or '').strip()
    auj = datetime.now().strftime('%Y-%m-%d')
    if not date_debut and not date_fin:
        date_debut = date_fin = auj

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM campagnes WHERE nom = 'EXOSPHERE_SFR'")
    campagne_row = c.fetchone()
    campagne_id = campagne_row[0] if campagne_row else 1

    sql = """
        SELECT DATE_SIGNATURE, CIVILITE_CLIENT, NOM_CLIENT, PRENOM_CLIENT,
               TELEPHONE, STATUT, AGENT, DEUXIEME_ADRESSE
        FROM clients
        WHERE campagne_id=?
    """
    params = [campagne_id]
    if date_debut and date_fin:
        sql += " AND DATE_SIGNATURE BETWEEN ? AND ?"
        params += [date_debut, date_fin]
    elif date_debut:
        sql += " AND DATE_SIGNATURE >= ?"
        params.append(date_debut)
    elif date_fin:
        sql += " AND DATE_SIGNATURE <= ?"
        params.append(date_fin)
    sql += " ORDER BY DATE_SIGNATURE DESC"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    columns = ["DATE_SIGNATURE","CIVILITE_CLIENT","NOM_CLIENT","PRENOM_CLIENT",
               "TELEPHONE","STATUT","AGENT","DEUXIEME_ADRESSE"]
    df = pd.DataFrame(rows, columns=columns)

    # Utilise un buffer mémoire => pas de fichiers temporaires qui trainent
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="SFR")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=f"export_sfr_{auj}.xlsx")


# ──────────────────────────────────────────────────────────────────────────────
# Journal / Présence / Live
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/journal')
def journal():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('dashboard'))

    agent = (request.args.get('agent', '') or '').strip()
    date_debut = (request.args.get('date_debut', '') or '').strip()
    date_fin   = (request.args.get('date_fin', '') or '').strip()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT agent_nom FROM journal_connexions")
    agents = [row[0] for row in c.fetchall()]

    auj = datetime.now().strftime('%Y-%m-%d')
    if not date_debut and not date_fin:
        date_debut = date_fin = auj

    sql = "SELECT agent_nom, date_connexion, page, type_event FROM journal_connexions WHERE 1=1"
    params = []
    if agent:
        sql += " AND agent_nom = ?"; params.append(agent)
    if date_debut:
        sql += " AND date_connexion >= ?"; params.append(date_debut + " 00:00:00")
    if date_fin:
        sql += " AND date_connexion <= ?"; params.append(date_fin + " 23:59:59")
    sql += " ORDER BY agent_nom, date_connexion ASC"
    c.execute(sql, params)
    all_logs = c.fetchall()
    conn.close()

    from collections import defaultdict
    logs_by_agent_by_day = defaultdict(lambda: defaultdict(list))
    for agent_nom, dt_conn, page, type_evt in all_logs:
        logs_by_agent_by_day[agent_nom][dt_conn[:10]].append((agent_nom, dt_conn, page, type_evt))

    filtered_logs = []
    for agent_name, days in logs_by_agent_by_day.items():
        for day, logs in days.items():
            first_conn = None
            last_event = None
            for l in logs:
                if l[3] == 'connexion' and not first_conn:
                    first_conn = l
                if l[3] in ['connexion', 'deconnexion']:
                    last_event = l
            if first_conn:
                filtered_logs.append(first_conn)
            if last_event and last_event != first_conn:
                filtered_logs.append(last_event)

    filtered_logs.sort(key=lambda x: (x[0], x[1]))
    return render_template('journal.html', logs=filtered_logs, agents=agents)


@app.route('/export_journal')
def export_journal():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('dashboard'))

    agent = (request.args.get('agent', '') or '').strip()
    date_debut = (request.args.get('date_debut', '') or '').strip()
    date_fin   = (request.args.get('date_fin', '') or '').strip()

    conn = sqlite3.connect(DB_NAME)
    sql = "SELECT agent_nom, date_connexion, page, type_event FROM journal_connexions WHERE 1=1"
    params = []
    if agent:
        sql += " AND agent_nom = ?"; params.append(agent)
    if date_debut:
        sql += " AND date_connexion >= ?"; params.append(date_debut + " 00:00:00")
    if date_fin:
        sql += " AND date_connexion <= ?"; params.append(date_fin + " 23:59:59")
    sql += " ORDER BY date_connexion DESC"

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    df.columns = ["Agent","Date/Heure","Page","Événement"]
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Journal")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="export_journal.xlsx")


@app.route('/journal_presence')
def journal_presence():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('dashboard'))

    agent = (request.args.get('agent', '') or '').strip()
    date_debut = (request.args.get('date_debut', '') or '')
    date_fin   = (request.args.get('date_fin', '') or '')

    auj_str = datetime.now().strftime('%Y-%m-%d')
    if not date_debut and not date_fin:
        date_debut = date_fin = auj_str
    elif not date_debut:
        date_debut = date_fin
    elif not date_fin:
        date_fin = date_debut

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT agent_nom FROM journal_connexions")
    agents = [row[0] for row in c.fetchall()]

    d1 = datetime.strptime(date_debut, '%Y-%m-%d')
    d2 = datetime.strptime(date_fin, '%Y-%m-%d')
    jours = []
    d = d1
    while d <= d2:
        jours.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)

    agents_a_afficher = [agent] if agent else agents

    tableau = []
    for ag in agents_a_afficher:
        for jour in jours:
            c.execute("""
                SELECT MIN(date_connexion), MAX(date_connexion)
                FROM journal_connexions
                WHERE agent_nom=? AND date_connexion>=? AND date_connexion<=?
                  AND (type_event='connexion' OR type_event='deconnexion')
            """, (ag, jour+" 00:00:00", jour+" 23:59:59"))
            entree, sortie = c.fetchone()
            heure_entree = entree[11:19] if entree else ''
            heure_sortie = sortie[11:19] if sortie else ''
            if entree and sortie:
                dt1 = datetime.strptime(entree, "%Y-%m-%d %H:%M:%S")
                dt2 = datetime.strptime(sortie, "%Y-%m-%d %H:%M:%S")
                duree = dt2 - dt1 if dt2 > dt1 else timedelta()
                h = int(duree.total_seconds() // 3600)
                m = int((duree.total_seconds() % 3600) // 60)
                duree_txt = f"{h:02d}:{m:02d}"
            else:
                duree_txt = ''
            tableau.append([ag, jour, heure_entree, heure_sortie, duree_txt])

    conn.close()
    return render_template('journal_presence.html',
                           tableau=tableau, jours=jours, agents=agents,
                           date_debut=date_debut, date_fin=date_fin, agent_selected=agent)


@app.route('/export_presence')
def export_presence():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('dashboard'))

    agent = (request.args.get('agent', '') or '').strip()
    date_debut = (request.args.get('date_debut', '') or '')
    date_fin   = (request.args.get('date_fin', '') or '')

    auj = datetime.now()
    if not date_debut:
        date_debut = auj.replace(day=1).strftime('%Y-%m-%d')
    if not date_fin:
        fin_mois = (auj.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        date_fin = fin_mois.strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT agent_nom FROM journal_connexions")
    agents = [row[0] for row in c.fetchall()]

    d1 = datetime.strptime(date_debut, '%Y-%m-%d')
    d2 = datetime.strptime(date_fin, '%Y-%m-%d')
    jours = []
    d = d1
    while d <= d2:
        jours.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)

    agents_a_exporter = [agent] if agent else agents

    donnees = []
    for ag in agents_a_exporter:
        for jour in jours:
            c.execute("""
                SELECT MIN(date_connexion), MAX(date_connexion)
                FROM journal_connexions
                WHERE agent_nom=? AND date_connexion>=? AND date_connexion<=?
                  AND (type_event='connexion' OR type_event='deconnexion')
            """, (ag, jour+" 00:00:00", jour+" 23:59:59"))
            entree, sortie = c.fetchone()
            heure_entree = entree[11:19] if entree else ''
            heure_sortie = sortie[11:19] if sortie else ''
            if entree and sortie:
                dt1 = datetime.strptime(entree, "%Y-%m-%d %H:%M:%S")
                dt2 = datetime.strptime(sortie, "%Y-%m-%d %H:%M:%S")
                duree = dt2 - dt1 if dt2 > dt1 else timedelta()
                h = int(duree.total_seconds() // 3600)
                m = int((duree.total_seconds() % 3600) // 60)
                duree_txt = f"{h:02d}:{m:02d}"
            else:
                duree_txt = ''
            donnees.append([ag, jour, heure_entree, heure_sortie, duree_txt])

    conn.close()

    df = pd.DataFrame(donnees, columns=["Agent", "Date", "Entrée", "Sortie", "Durée"])
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Présence")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="presence_lignes.xlsx")


@app.route('/live_agents')
def live_agents():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        flash("Accès réservé à l'administration/supervision.", "danger")
        return redirect(url_for('dashboard'))

    date_auj = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT NOM FROM agents")
    agents = [row[0] for row in c.fetchall()]
    live_data = []

    for ag in agents:
        c.execute("""
            SELECT type_event, date_connexion
            FROM journal_connexions
            WHERE agent_nom=? AND date_connexion>=?
            ORDER BY date_connexion DESC LIMIT 1
        """, (ag, date_auj + " 00:00:00"))
        last = c.fetchone()
        statut = "Déconnecté"
        heure_statut = ""
        if last:
            type_event, date_evt = last
            heure_statut = date_evt[11:16]
            if type_event == 'connexion':
                statut = "Connecté"
            elif type_event == 'pause':
                statut = "En pause"
            elif type_event == 'saisie':
                statut = "Saisie en cours"
            elif type_event == 'deconnexion':
                statut = "Déconnecté"

        c.execute("""
            SELECT MIN(date_connexion) FROM journal_connexions
            WHERE agent_nom=? AND date_connexion>=? AND type_event='connexion'
        """, (ag, date_auj + " 00:00:00"))
        entree = c.fetchone()[0]
        heure_connexion = entree[11:16] if entree else ""

        c.execute("SELECT COUNT(*) FROM clients WHERE AGENT=? AND DATE_SIGNATURE=?", (ag, date_auj))
        nb_clients = c.fetchone()[0] or 0

        live_data.append({
            "agent": ag,
            "statut": statut,
            "heure_statut": heure_statut,
            "heure_connexion": heure_connexion,
            "nb_clients": nb_clients
        })

    conn.close()
    return render_template('live_agents.html', live_data=live_data, date_auj=date_auj)


@app.route('/api/live_agents')
def api_live_agents():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        return {"error": "forbidden"}, 403

    date_auj = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT NOM FROM agents")
    agents = [row[0] for row in c.fetchall()]
    live_data = []

    for ag in agents:
        c.execute("""
            SELECT type_event, date_connexion
            FROM journal_connexions
            WHERE agent_nom=? AND date_connexion>=?
            ORDER BY date_connexion DESC LIMIT 1
        """, (ag, date_auj + " 00:00:00"))
        last = c.fetchone()
        statut = "Déconnecté"
        heure_statut = ""
        if last:
            type_event, date_evt = last
            heure_statut = date_evt[11:16]
            if type_event == 'connexion':
                statut = "Connecté"
            elif type_event == 'pause':
                statut = "En pause"
            elif type_event == 'saisie':
                statut = "Saisie en cours"
            elif type_event == 'deconnexion':
                statut = "Déconnecté"

        c.execute("""
            SELECT MIN(date_connexion) FROM journal_connexions
            WHERE agent_nom=? AND date_connexion>=? AND type_event='connexion'
        """, (ag, date_auj + " 00:00:00"))
        entree = c.fetchone()[0]
        heure_connexion = entree[11:16] if entree else ""

        c.execute("SELECT COUNT(*) FROM clients WHERE AGENT=? AND DATE_SIGNATURE=?", (ag, date_auj))
        nb_clients = c.fetchone()[0] or 0

        live_data.append({
            "agent": ag,
            "statut": statut,
            "heure_statut": heure_statut,
            "heure_connexion": heure_connexion,
            "nb_clients": nb_clients
        })

    conn.close()
    return {"live_data": live_data}


@app.route('/classement_agents')
def classement_agents():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT AGENT, COUNT(*) as nb_valides
        FROM clients
        WHERE STATUT = 'valide'
          AND strftime('%Y-%m', DATE_SIGNATURE) = strftime('%Y-%m', 'now')
        GROUP BY AGENT
        ORDER BY nb_valides DESC
        LIMIT 5
    """)
    classement = c.fetchall()
    conn.close()
    total_general = sum([row[1] for row in classement]) if classement else 0
    return render_template('classement_agents.html',
                           classement=classement, total_general=total_general)


def notifier_nouveau_client(nom_client):
    socketio.emit('nouveau_client', {'message': f"Nouveau client : {nom_client}"})


@app.route('/overview')
def overview():
    date_debut = (request.args.get('date_debut', '') or '').strip()
    date_fin   = (request.args.get('date_fin', '') or '').strip()
    agent_sfr = (request.args.get('agent_sfr', '') or '').strip()
    agent_valandre = (request.args.get('agent_valandre', '') or '').strip()
    agent_huma = (request.args.get('agent_huma', '') or '').strip()

    auj = datetime.now().strftime('%Y-%m-%d')
    if not date_debut and not date_fin:
        date_debut = date_fin = auj
    elif not date_debut:
        date_debut = date_fin
    elif not date_fin:
        date_fin = date_debut

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT DISTINCT AGENT FROM clients WHERE campagne_id=1")
    agents_sfr = sorted({row[0] for row in c.fetchall() if row[0]})
    c.execute("SELECT DISTINCT AGENT FROM clients WHERE campagne_id=2")
    agents_valandre = sorted({row[0] for row in c.fetchall() if row[0]})

    # SFR
    campagne_id_sfr = 1
    params = [campagne_id_sfr, date_debut, date_fin]
    agent_filter = ""
    if agent_sfr:
        agent_filter = " AND AGENT=?"
        params.append(agent_sfr)

    c.execute(f"""
        SELECT SUM(CASE WHEN STATUT='valide' THEN 1 ELSE 0 END),
               SUM(CASE WHEN STATUT='non valide' THEN 1 ELSE 0 END)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter}
    """, params)
    sfr_valide, sfr_non_valide = c.fetchone() or (0, 0)

    c.execute(f"""
        SELECT strftime('%H', DATE_MODIF), COUNT(*)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter}
        GROUP BY strftime('%H', DATE_MODIF)
        ORDER BY strftime('%H', DATE_MODIF)
    """, params)
    sfr_par_heure = c.fetchall()

    c.execute(f"""
        SELECT AGENT, strftime('%H', DATE_MODIF), COUNT(*)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter}
        GROUP BY AGENT, strftime('%H', DATE_MODIF)
        ORDER BY AGENT, strftime('%H', DATE_MODIF)
    """, params)
    sfr_agent_par_heure = c.fetchall()

    # VALANDRE
    campagne_id_valandre = 2
    params2 = [campagne_id_valandre, date_debut, date_fin]
    agent_filter2 = ""
    if agent_valandre:
        agent_filter2 = " AND AGENT=?"
        params2.append(agent_valandre)

    c.execute(f"""
        SELECT
            SUM(CASE WHEN STRATO_STATUT='VALIDÉ' OR LSR_STATUT='VALIDÉ' OR PRESSE_STATUT='VALIDÉ'
                     OR ENI_STATUT='VALIDÉ' OR SERENITY_STATUT='VALIDÉ' OR PROTEC_ALLIANCE_STATUT='VALIDÉ'
                     OR WEKIWI_STATUT='VALIDÉ' THEN 1 ELSE 0 END),
            SUM(CASE WHEN (STRATO_STATUT!='VALIDÉ' AND LSR_STATUT!='VALIDÉ' AND PRESSE_STATUT!='VALIDÉ'
                           AND ENI_STATUT!='VALIDÉ' AND SERENITY_STATUT!='VALIDÉ' AND PROTEC_ALLIANCE_STATUT!='VALIDÉ'
                           AND WEKIWI_STATUT!='VALIDÉ') THEN 1 ELSE 0 END)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter2}
    """, params2)
    valandre_valide, valandre_non_valide = c.fetchone() or (0, 0)

    c.execute(f"""
        SELECT strftime('%H', DATE_MODIF), COUNT(*)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter2}
        GROUP BY strftime('%H', DATE_MODIF)
        ORDER BY strftime('%H', DATE_MODIF)
    """, params2)
    valandre_par_heure = c.fetchall()

    c.execute(f"""
        SELECT AGENT, strftime('%H', DATE_MODIF), COUNT(*)
        FROM clients
        WHERE campagne_id=? AND DATE_SIGNATURE>=? AND DATE_SIGNATURE<=?{agent_filter2}
        GROUP BY AGENT, strftime('%H', DATE_MODIF)
        ORDER BY AGENT, strftime('%H', DATE_MODIF)
    """, params2)
    valandre_agent_par_heure = c.fetchall()

    conn.close()

    # HUMANITAIRE
    db_path_huma = HUMA_DB_NAME

    with sqlite3.connect(db_path_huma) as con_huma:
        con_huma.row_factory = sqlite3.Row
        # liste agents
        agents_huma = [r["agent"] for r in con_huma.execute(
            "SELECT DISTINCT agent FROM calls WHERE agent IS NOT NULL AND agent<>'' ORDER BY agent"
        )]

        params_huma = [date_debut, date_fin]
        extra_huma = ""
        if agent_huma:
            extra_huma = " AND agent = ? "
            params_huma.append(agent_huma)

        df_huma = pd.read_sql(f"""
            SELECT agent, call_date, lib_status, COALESCE(don_raw, montant, 0) AS don_val
            FROM calls
            WHERE call_date >= ? AND call_date <= ? {extra_huma}
        """, con_huma, params=params_huma)

        # heures GRH
        grh_huma = pd.read_sql("""
            SELECT COALESCE(heures,0) AS heures
            FROM grh_hours
            WHERE jour >= ? AND jour <= ?
        """, con_huma, params=(date_debut, date_fin))

        # pour le graph par heure
        huma_agent_par_heure = [
            [row["agent"], row["heure"], row["nb"]]
            for row in con_huma.execute("""
                SELECT agent,
                       strftime('%H', call_date || ' 00:00:00') AS heure,
                       COUNT(*) AS nb
                FROM calls
                WHERE call_date BETWEEN ? AND ?
                GROUP BY agent, heure
            """, (date_debut, date_fin))
        ]

    # Calcul des stats humanitaires
    total_heures_prod = float(grh_huma["heures"].sum()) if not grh_huma.empty else 0.0

    if df_huma.empty:
        huma_stats = {
            "total_heures_prod": total_heures_prod,
            "total_cu": 0,
            "total_don": 0,
            "don_moyen": 0.0,
            "chiffre_affaire": 0.0,
        }
    else:
        df_huma["lib_status_norm"] = df_huma["lib_status"].fillna("").str.strip()
        df_huma["is_cu"] = df_huma["lib_status_norm"].isin(CU_STATUSES).astype(int)
        df_huma["is_don"] = df_huma["lib_status_norm"].isin(DON_STATUSES).astype(int)

        def _montant_if_don(row):
            try:
                v = float(row["don_val"])
            except Exception:
                v = 0.0
            return v if row["lib_status_norm"] in DON_STATUSES else 0.0

        df_huma["montant_don"] = df_huma.apply(_montant_if_don, axis=1)

        total_cu = int(df_huma["is_cu"].sum())
        total_don = int(df_huma["is_don"].sum())
        total_montant = float(df_huma["montant_don"].sum())
        don_moyen = (total_montant / total_don) if total_don > 0 else 0.0
        tx_daccord = (total_don / total_cu) if total_cu > 0 else 0.0

        # chiffre d'affaire estimé
        ca = calculer_chiffre_affaire_dyn(
            don_moyen=don_moyen,
            tx_daccord=tx_daccord,
            total_don=total_don,
            path_excel=os.path.join("Data", "inbox", "BDD QUALICONTACT.xlsx")
        )

        huma_stats = {
            "total_heures_prod": total_heures_prod,
            "total_cu": total_cu,
            "total_don": total_don,
            "don_moyen": don_moyen,
            "chiffre_affaire": ca,
        }

    return render_template(
        'overview.html',
        sfr_valide=sfr_valide or 0,
        sfr_non_valide=sfr_non_valide or 0,
        sfr_par_heure=sfr_par_heure,
        sfr_agent_par_heure=sfr_agent_par_heure,
        valandre_valide=valandre_valide or 0,
        valandre_non_valide=valandre_non_valide or 0,
        valandre_par_heure=valandre_par_heure,
        valandre_agent_par_heure=valandre_agent_par_heure,
        agents_sfr=agents_sfr,
        agents_valandre=agents_valandre,
        agents_huma=agents_huma,
        agent_sfr=agent_sfr,
        agent_valandre=agent_valandre,
        agent_huma=agent_huma,
        huma_stats=huma_stats,
        huma_agent_par_heure=huma_agent_par_heure,
        date_debut=date_debut,
        date_fin=date_fin,
        auj=auj
    )


def file_size_okay(file):
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    return size <= 5 * 1024 * 1024


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'jpg', 'jpeg', 'png'}


# ──────────────────────────────────────────────────────────────────────────────
# Chat SocketIO
# ──────────────────────────────────────────────────────────────────────────────
@socketio.on('chat_message')
def handle_chat_message(data):
    if 'user' in data and 'message' in data:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (user, message) VALUES (?, ?)", (data['user'], data['message']))
        conn.commit()
        conn.close()
        # broadcast = True -> envoie à tous les clients
        socketio.emit('chat_message', data, broadcast=True)


@socketio.on('chat_history_request')
def handle_chat_history_request():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user, message, timestamp FROM chat_messages ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    messages = [{'user': row[0], 'message': row[1], 'timestamp': row[2]} for row in reversed(rows)]
    socketio.emit('chat_history', messages)


# ──────────────────────────────────────────────────────────────────────────────
# Export générique clients
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/export_clients')
def export_clients():
    agent = request.args.get('agent')
    statut = request.args.get('statut')
    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    sql = "SELECT * FROM clients"
    filters, params = [], []

    if agent:
        filters.append("AGENT = ?"); params.append(agent)
    if statut:
        filters.append("STATUT = ?"); params.append(statut)
    if date_debut and date_fin:
        filters.append("DATE_SIGNATURE BETWEEN ? AND ?"); params.extend([date_debut, date_fin])
    elif date_debut:
        filters.append("DATE_SIGNATURE >= ?"); params.append(date_debut)
    elif date_fin:
        filters.append("DATE_SIGNATURE <= ?"); params.append(date_fin)

    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY DATE_SIGNATURE DESC"

    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Clients")
    out.seek(0)
    return send_file(out, download_name="export_clients.xlsx", as_attachment=True)


@app.route("/admin/import_primes_huma", methods=["POST", "GET"])
def admin_import_primes_huma():
    if session.get('agent_role') not in ['admin', 'superviseur']:
        return jsonify({"error": "Accès réservé"}), 403

    excel_path = request.args.get("path") or r"/mnt/data/Prime don.xlsx"
    try:
        dfp = pd.read_excel(excel_path, sheet_name="Prime dons").fillna(0)
        rename_map = {
            "Nombre de dons par mois": "dons_cible",
            "Don Moyen": "don_moyen_cible",
            "Prime en euros": "prime_eur",
            "primes en dt": "prime_dt"
        }
        dfp = dfp.rename(columns=rename_map)[list(rename_map.values())].copy()
        dfp["dons_cible"] = pd.to_numeric(dfp["dons_cible"], errors="coerce").fillna(0).astype(int)
        dfp["don_moyen_cible"] = pd.to_numeric(dfp["don_moyen_cible"], errors="coerce").fillna(0).astype(int)
        dfp["prime_eur"] = pd.to_numeric(dfp["prime_eur"], errors="coerce").fillna(0.0).astype(float)
        dfp["prime_dt"]  = pd.to_numeric(dfp["prime_dt"],  errors="coerce").fillna(0.0).astype(float)

        conn = sqlite3.connect(DB_NAME); c = conn.cursor()
        c.execute("DELETE FROM primes_huma")
        conn.commit()
        c.executemany("""
            INSERT INTO primes_huma (dons_cible, don_moyen_cible, prime_eur, prime_dt)
            VALUES (?, ?, ?, ?)
        """, list(dfp.itertuples(index=False, name=None)))
        conn.commit(); conn.close()

        return jsonify({"status": "ok", "rows": len(dfp)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/refresh_import")
def admin_refresh_import():
    # si tu as une session / login
    if "agent_nom" not in session:
        return redirect(url_for("login"))

    con = sqlite3.connect(HUMA_DB_NAME)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_schema_incremental(con)

    inbox_appels = INBOX_APPELS
    archive_appels = ARCHIVE_APPELS
    grh_glob    = os.path.join(DATA_DIR, "extract_grh*.xlsx")

    n1 = import_appels_incremental(con, inbox_appels, archive_dir=archive_appels)
    n2 = import_grh_incremental(con, grh_glob)
    con.close()

    # plus tard tu pourras faire un flash ici
    return redirect(url_for("dashboard_humanitaire"))


@app.route("/is_logged_in")
def is_logged_in():
    return ("", 204) if session.get("agent_nom") else ("", 401)


@app.route("/export_excel_humanitaire")
def export_excel_humanitaire():
    from humanitaire import export_dashboard_humanitaire_xlsx
    date_debut = (request.args.get('date_debut','') or '').strip() or None
    date_fin   = (request.args.get('date_fin','') or '').strip() or None
    base_code  = (request.args.get('base','') or '').strip().upper() or None
    try:
        buf = export_dashboard_humanitaire_xlsx(
            appels_glob=None, grh_glob=None,
            date_debut=date_debut, date_fin=date_fin, base_code=base_code
        )
        return send_file(
            buf, as_attachment=True,
            download_name="export_dashboard_humanitaire.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"<pre>Erreur Export Excel Humanitaire:\n{e}</pre>", 500


# ──────────────────────────────────────────────────────────────────────────────
# Aircall: téléchargement / debug / cache
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/telecharger_aircall_numero/<phone_number>', methods=['POST'])
def telecharger_aircall_numero(phone_number):
    if 'agent_nom' not in session:
        flash("Session expirée.", "warning")
        return redirect(url_for('login'))

    clean = _normalize_phone(phone_number)

    try:
        recording_url = find_recording_for_phone_number(clean)
        if not recording_url:
            update_call_id_in_db(clean)
            flash("Aucun enregistrement trouvé pour ce numéro.", "warning")
            return redirect(url_for('dashboard'))

        r = requests.get(recording_url, timeout=60)
        if r.status_code != 200:
            flash(f"Téléchargement impossible (HTTP {r.status_code}).", "danger")
            return redirect(url_for('dashboard'))

        unique_id = uuid.uuid4().hex[:8]
        safe_number = re.sub(r"[^\d+]", "_", clean)
        filename = f"aircall_{safe_number}_{unique_id}.mp3"

        update_call_id_in_db(clean)

        buf = BytesIO(r.content); buf.seek(0)
        resp = send_file(buf, as_attachment=True, download_name=filename, mimetype='audio/mpeg')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    except Exception as e:
        print(f"[telecharger_aircall_numero] Erreur: {e}")
        flash("Erreur lors du téléchargement de l'enregistrement.", "danger")
        return redirect(url_for('dashboard'))


@app.route('/favicon.ico')
def favicon():
    return ('', 204)


@app.route('/debug_aircall/<phone_number>')
def debug_aircall(phone_number):
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        return jsonify({"error": "Accès interdit"}), 403

    info = {"phone_number": str(phone_number), "timestamp": datetime.now().isoformat()}
    try:
        recording_url = find_recording_for_phone_number(phone_number)
        info["recording_url"] = recording_url
        if recording_url:
            try:
                head = requests.head(recording_url, timeout=10)
                info["url_status"] = head.status_code
                info["content_type"] = head.headers.get('Content-Type')
                info["content_length"] = head.headers.get('Content-Length')
            except Exception as e:
                info["url_head_error"] = str(e)
    except Exception as e:
        info["error"] = str(e)
        return jsonify(info), 500

    return jsonify(info)


@app.route('/telecharger_aircall_test/<phone_number>', methods=['POST'])
def telecharger_aircall_test(phone_number):
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    try:
        recording_url = find_recording_for_phone_number(phone_number)
        if not recording_url:
            flash("Aucun enregistrement trouvé pour ce numéro de téléphone.", "warning")
            return redirect(url_for('dashboard'))

        resp = requests.get(recording_url, timeout=60)
        if resp.status_code == 200:
            safe_number = re.sub(r"[^\d+]", "_", str(phone_number))
            unique_id = uuid.uuid4().hex[:8]
            filename = f"test_{safe_number}_{unique_id}.mp3"
            file_stream = BytesIO(resp.content); file_stream.seek(0)
            return send_file(file_stream, as_attachment=True, download_name=filename, mimetype='audio/mpeg')
        else:
            flash(f"Erreur HTTP {resp.status_code}", "danger")
    except Exception as e:
        print(f"[telecharger_aircall_test] Erreur: {e}")
        flash(f"Erreur test : {e}", "danger")

    return redirect(url_for('dashboard'))


@app.route('/clear_aircall_cache', methods=['POST'])
def clear_aircall_cache():
    if session.get('agent_role', '') not in ['admin', 'superviseur']:
        return jsonify({"error": "Accès interdit"}), 403
    import gc
    gc.collect()
    flash("Cache vidé (si applicable).", "success")
    return redirect(url_for('dashboard'))


@app.route('/resolve_call_id/<phone_number>', methods=['POST', 'GET'])
def resolve_call_id(phone_number):
    if 'agent_nom' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    clean = _normalize_phone(phone_number)
    call_id = update_call_id_in_db(clean)
    return jsonify({"phone_number": clean, "call_id": call_id, "updated": bool(call_id)})


@app.route('/admin/backfill_call_ids', methods=['POST'])
def backfill_call_ids():
    if session.get('agent_role') not in ['admin', 'superviseur']:
        return jsonify({"error": "forbidden"}), 403

    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("""
        SELECT DISTINCT TELEPHONE
          FROM clients
         WHERE TELEPHONE IS NOT NULL AND TELEPHONE <> ''
           AND (CALL_ID IS NULL OR CALL_ID = '')
    """)
    phones = [row[0] for row in c.fetchall()]
    conn.close()

    updated = []
    for p in phones:
        cid = update_call_id_in_db(p)
        if cid:
            updated.append({"phone": _normalize_phone(p), "call_id": cid})

    return jsonify({"updated_count": len(updated), "details": updated})


@app.route('/telecharger_aircall_call/<call_id>', methods=['POST', 'GET'])
def telecharger_aircall_call(call_id):
    if 'agent_nom' not in session:
        flash("Session expirée.", "warning")
        return redirect(url_for('login'))

    try:
        r_call = requests.get(
            f"https://api.aircall.io/v1/calls/{call_id}",
            auth=HTTPBasicAuth(API_ID, API_TOKEN), timeout=20
        )
        r_call.raise_for_status()
        call_data = r_call.json().get('call') or {}
        rec_url = call_data.get('recording')
        if not rec_url:
            flash("Aucun enregistrement pour cet appel.", "warning")
            return redirect(url_for('dashboard'))

        r_file = requests.get(rec_url, timeout=60)
        if r_file.status_code != 200:
            flash(f"Impossible de télécharger (HTTP {r_file.status_code}).", "danger")
            return redirect(url_for('dashboard'))

        unique_id = uuid.uuid4().hex[:8]
        filename = f"aircall_call_{call_id}_{unique_id}.mp3"

        buf = BytesIO(r_file.content); buf.seek(0)
        resp = send_file(buf, as_attachment=True, download_name=filename, mimetype='audio/mpeg')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    except Exception as e:
        print(f"[telecharger_aircall_call] Erreur: {e}")
        flash("Erreur lors du téléchargement de l'enregistrement.", "danger")
        return redirect(url_for('dashboard'))


@app.route('/resolve_call_id_for_client/<int:client_id>', methods=['POST'])
def resolve_call_id_for_client(client_id):
    if 'agent_nom' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = sqlite3.connect(DB_NAME); c = conn.cursor()
        c.execute("SELECT TELEPHONE FROM clients WHERE id=?", (client_id,))
        row = c.fetchone()
        conn.close()
        if not row or not row[0]:
            return jsonify({"client_id": client_id, "updated": False, "reason": "no phone"}), 200

        phone = _normalize_phone(row[0])
        call_id = update_call_id_in_db(phone)
        return jsonify({"client_id": client_id, "phone": phone, "call_id": call_id, "updated": bool(call_id)}), 200
    except Exception as e:
        return jsonify({"client_id": client_id, "error": str(e)}), 500


@app.route('/play_aircall/<phone_number>')
def play_aircall(phone_number):
    if 'agent_nom' not in session:
        return redirect(url_for('login'))
    try:
        rec_url = find_recording_for_phone_number(_normalize_phone(phone_number))
        if not rec_url:
            flash("Aucun enregistrement trouvé pour ce numéro.", "warning")
            return redirect(url_for('dashboard'))

        r = requests.get(rec_url, timeout=60)
        if r.status_code != 200:
            flash(f"Lecture impossible (HTTP {r.status_code}).", "danger")
            return redirect(url_for('dashboard'))

        buf = BytesIO(r.content); buf.seek(0)
        resp = send_file(buf, as_attachment=False, download_name="aircall_preview.mp3", mimetype='audio/mpeg')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        resp.headers['Content-Disposition'] = 'inline; filename=\"aircall_preview.mp3\"'
        return resp
    except Exception as e:
        print(f"[play_aircall] Erreur: {e}")
        flash("Erreur pendant la lecture de l'enregistrement.", "danger")
        return redirect(url_for('dashboard'))


@app.errorhandler(429)
def ratelimit_handler(e):
    flash("Trop de tentatives de connexion. Réessayez dans 1 minute.", "danger")
    return redirect(url_for('login'))


@app.route("/admin/huma_sync_agents")
def admin_huma_sync_agents():
    if session.get('agent_role') not in ['admin', 'superviseur']:
        return jsonify({"error": "Accès réservé à l'administration/supervision"}), 403

    from humanitaire import extract_tv_list

    appels = request.args.get("appels")
    dry = request.args.get("dry") == "1"
    confirm = request.args.get("confirm") == "1"

    try:
        tvs = extract_tv_list(appels)

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT NOM, LOGIN FROM agents")
        rows = c.fetchall()
        existing_names = {r[0] for r in rows if r[0]}
        existing_logins = {r[1] for r in rows if r[1]}

        camp_id = get_campagne_id_by_name("HUMANITAIRE")
        if not camp_id:
            return jsonify({"error": "Campagne HUMANITAIRE introuvable en base."}), 500

        will_create, skipped = [], []
        for tv in tvs:
            if tv in existing_names:
                skipped.append({"tv": tv, "reason": "NOM déjà présent"})
                continue

            base_login = _slugify_login_from_tv(tv)
            login = _ensure_unique_login(base_login, existing_logins)

            pwd_plain = "ChangeMe#2025"
            will_create.append({
                "nom": tv,
                "login": login,
                "password_temp": pwd_plain,
                "role": "agent",
                "campagne_id": camp_id,
            })

        created = []
        if confirm and will_create:
            for row in will_create:
                hashed = bcrypt.hashpw(row["password_temp"].encode("utf-8"), bcrypt.gensalt())
                try:
                    c.execute(
                        "INSERT INTO agents (NOM, LOGIN, MDP, ROLE, campagne_id) VALUES (?, ?, ?, ?, ?)",
                        (row["nom"], row["login"], hashed, row["role"], row["campagne_id"])
                    )
                    conn.commit()
                    existing_names.add(row["nom"])
                    existing_logins.add(row["login"])
                    created.append({"nom": row["nom"], "login": row["login"]})
                except sqlite3.IntegrityError as e:
                    skipped.append({"tv": row["nom"], "reason": f"Intégrité: {e}"})
            conn.close()

            return jsonify({
                "status": "ok",
                "created_count": len(created),
                "created": created,
                "skipped_count": len(skipped),
                "skipped": skipped
            }), 200
        else:
            conn.close()
            return jsonify({
                "status": "preview" if dry or not confirm else "noop",
                "will_create_count": len(will_create),
                "will_create": will_create,
                "skipped_count": len(skipped),
                "skipped": skipped,
                "how_to_create": "/admin/huma_sync_agents?confirm=1"
            }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/dashboard_ca_projets")
def dashboard_ca_projets():
    if "agent_nom" not in session:
        return redirect(url_for("login"))

    period = request.args.get("period", "day")  # "day" ou "month"
    # on affiche le mois courant par défaut
    today = date.today()
    mois_courant = today.strftime("%Y-%m")

    crm_db = DB_NAME

    # -----------------------------
    # 1) SFR & VALANDRE (données brutes)
    # -----------------------------
    # on va récupérer les lignes avec leur date
    df_projets = pd.DataFrame()
    if os.path.exists(crm_db):
        with sqlite3.connect(crm_db) as con:
            # SFR
            try:
                df_sfr = pd.read_sql("""
                    SELECT 
                        c.id,
                        'SFR' AS projet,
                        COALESCE(c.date_saisie, c.created_at, c.date_creation) AS dt
                    FROM clients c
                    JOIN campagnes cp ON cp.id = c.campagne_id
                    WHERE cp.nom = 'EXOSPHERE_SFR'
                """, con)
            except Exception:
                df_sfr = pd.DataFrame(columns=["id", "projet", "dt"])

            # VALANDRE dans clients
            try:
                df_val = pd.read_sql("""
                    SELECT 
                        c.id,
                        'VALANDRE' AS projet,
                        COALESCE(c.date_saisie, c.created_at, c.date_creation) AS dt
                    FROM clients c
                    JOIN campagnes cp ON cp.id = c.campagne_id
                    WHERE cp.nom = 'VALANDRE'
                """, con)
            except Exception:
                df_val = pd.DataFrame(columns=["id", "projet", "dt"])

            # VALANDRE historique
            # (on lui met une date vide si pas de colonne, tu pourras l'ajouter après)
            try:
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "clients_valandre" in tables:
                    df_val_hist = pd.read_sql("""
                        SELECT 
                            id,
                            'VALANDRE' AS projet,
                            COALESCE(date_saisie, created_at, date('now')) AS dt
                        FROM clients_valandre
                    """, con)
                else:
                    df_val_hist = pd.DataFrame(columns=["id", "projet", "dt"])
            except Exception:
                df_val_hist = pd.DataFrame(columns=["id", "projet", "dt"])

        df_projets = pd.concat([df_sfr, df_val, df_val_hist], ignore_index=True)

    # normaliser la date
    if not df_projets.empty:
        df_projets["dt"] = pd.to_datetime(df_projets["dt"], errors="coerce")
        df_projets = df_projets.dropna(subset=["dt"])
        df_projets["day"] = df_projets["dt"].dt.date.astype(str)
        df_projets["month"] = df_projets["dt"].dt.to_period("M").astype(str)
    else:
        df_projets["projet"] = []
        df_projets["day"] = []
        df_projets["month"] = []

    # tarifs
    TARIFS = {
        "SFR": 3.0,
        "VALANDRE": 4.0,
    }

    # agrégat par période
    if period == "month":
        grp = df_projets.groupby(["month", "projet"]).size().reset_index(name="nb")
        # on garde que le mois courant si tu veux
        # grp = grp[grp["month"] == mois_courant]
        grp["ca"] = grp.apply(lambda r: r["nb"] * TARIFS.get(r["projet"], 0), axis=1)
        rows_projets = grp.sort_values(["month", "projet"]).to_dict(orient="records")
    else:  # day
        grp = df_projets.groupby(["day", "projet"]).size().reset_index(name="nb")
        grp["ca"] = grp.apply(lambda r: r["nb"] * TARIFS.get(r["projet"], 0), axis=1)
        rows_projets = grp.sort_values(["day", "projet"]).to_dict(orient="records")

    # CA global "instantané" (toutes lignes)
    nb_sfr = int((df_projets["projet"] == "SFR").sum())
    nb_valandre = int((df_projets["projet"] == "VALANDRE").sum())
    ca_sfr = nb_sfr * TARIFS["SFR"]
    ca_valandre = nb_valandre * TARIFS["VALANDRE"]

    # -----------------------------
    # 2) HUMANITAIRE par association (comme avant)
    # -----------------------------
    huma_db = HUMA_DB_NAME
    asso_rows = []
    if os.path.exists(huma_db):
        with sqlite3.connect(huma_db) as con:
            df_h = pd.read_sql("""
                SELECT
                    COALESCE(base,'') AS base,
                    COALESCE(lib_status,'') AS lib_status,
                    COALESCE(don_raw, montant, 0) AS don_val
                FROM calls
                WHERE base IS NOT NULL AND base <> ''
            """, con)

        if not df_h.empty:
            df_h["asso"] = df_h["base"].str.slice(0, 3).str.upper()
            df_h["is_cu"] = df_h["lib_status"].isin(CU_STATUSES).astype(int)
            df_h["is_don"] = df_h["lib_status"].isin(DON_STATUSES).astype(int)

            tmp = []
            for asso, g in df_h.groupby("asso"):
                total_cu = int(g["is_cu"].sum())
                total_don = int(g["is_don"].sum())
                montant_don = 0.0
                if total_don > 0:
                    for v in g.loc[g["is_don"] == 1, "don_val"]:
                        try:
                            montant_don += float(v)
                        except Exception:
                            pass
                    don_moyen = montant_don / total_don
                else:
                    don_moyen = 0.0
                tx_daccord = (total_don / total_cu) if total_cu > 0 else 0.0
                try:
                    ca_asso = calculer_chiffre_affaire_dyn(
                        don_moyen=don_moyen,
                        tx_daccord=tx_daccord,
                        total_don=total_don,
                        path_excel=os.path.join("Data", "inbox", "BDD QUALICONTACT.xlsx")
                    )
                except Exception:
                    ca_asso = 0.0

                tmp.append({
                    "asso": asso,
                    "total_cu": total_cu,
                    "total_don": total_don,
                    "don_moyen": don_moyen,
                    "tx_daccord": tx_daccord,
                    "ca": ca_asso,
                })
            asso_rows = sorted(tmp, key=lambda x: x["ca"], reverse=True)

    ca_total = ca_sfr + ca_valandre + sum(a["ca"] for a in asso_rows)

    return render_template(
        "dashboard_ca_projets.html",
        auj=today.isoformat(),
        period=period,
        rows_projets=rows_projets,
        ca_sfr=ca_sfr,
        ca_valandre=ca_valandre,
        nb_sfr=nb_sfr,
        nb_valandre=nb_valandre,
        asso_rows=asso_rows,
        ca_total=ca_total,
    )



# ------------------------------------------------------------
# Routes: listes des clients par campagne (SFR / Valandre)
# Vue SQLite requise: clients_sfr, clients_valandre_view
# ------------------------------------------------------------


def _query_rows(view_name: str, page: int = 1, per_page: int = 50, q: str = ""):
    """Lit une vue SQLite (clients_sfr ou clients_valandre_view) avec pagination et recherche simple."""
    offset = max(0, (page - 1) * per_page)
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # total
    total = cur.execute(f"SELECT COUNT(*) FROM {view_name}").fetchone()[0]

    # filtre recherche (nom/prenom/tel si 'q' fourni)
    where = ""
    params = []
    if q:
        where = "WHERE (COALESCE(NOM_CLIENT,'') LIKE ? OR COALESCE(PRENOM_CLIENT,'') LIKE ? OR COALESCE(TELEPHONE,'') LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])

    # Ordre: on essaie DATE_SIGNATURE puis id, selon dispo
    # NB: SQLite ignore les colonnes inconnues si on les traite via CASE dans ORDER BY
    order_clause = """
    ORDER BY 
        CASE WHEN (SELECT 1 FROM pragma_table_info(?) WHERE name='DATE_SIGNATURE') THEN DATE_SIGNATURE END DESC,
        id DESC
    """
    # On ne peut pas paramétrer un identifiant dans PRAGMA_TABLE_INFO (?) -> simplifions:
    # On teste la présence de la colonne via pragma pour choisir l'ORDER BY en Python.
    has_date_signature = False
    try:
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({view_name})").fetchall()]
        has_date_signature = "DATE_SIGNATURE" in cols
    except Exception:
        pass
    order_by = "ORDER BY DATE_SIGNATURE DESC, id DESC" if has_date_signature else "ORDER BY id DESC"

    sql = f"SELECT * FROM {view_name} {where} {order_by} LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    rows = cur.execute(sql, params).fetchall()

    con.close()
    return total, rows


@app.route("/clients/sfr")
def clients_sfr():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    q = request.args.get("q", "").strip()
    total, rows = _query_rows("clients_sfr", page=page, per_page=per_page, q=q)
    return render_template(
        "clients_list.html",
        title="Clients SFR",
        rows=rows, page=page, per_page=per_page, total=total, q=q,
        base_path="/clients/sfr"
    )


@app.route("/clients/valandre")
def clients_valandre():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    q = request.args.get("q", "").strip()
    total, rows = _query_rows("clients_valandre_view", page=page, per_page=per_page, q=q)
    return render_template(
        "clients_list.html",
        title="Clients Valandre",
        rows=rows, page=page, per_page=per_page, total=total, q=q,
        base_path="/clients/valandre"
    )


# ... existing code ...


# ──────────────────────────────────────────────────────────────
# Entrée principale
# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    socketio.run(app, debug=True)