"""Database schema helpers for the CRM application.

This module centralises all the logic required to bootstrap and migrate the
SQLite databases used by the CRM.  The project historically duplicated the
same set of helpers in several places (``app.py`` and its backups), which made
the initialisation path harder to follow and increased the odds of having
slightly different schemas depending on which definition happened to be
imported first.  By consolidating the code here we provide:

* A single source of truth for the CRM schema (``clients``/``agents`` tables
  and related metadata).
* Consistent creation of the ``primes_huma`` table used by the humanitarian
  dashboards.
* A helper for the dedicated humanitarian database.

All functions are idempotent: they can safely be executed multiple times
without altering existing data.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3
from typing import Iterable


@contextmanager
def _connect(db_path: str) -> Iterable[sqlite3.Connection]:
    """Yield a SQLite connection and guarantee it is closed afterwards."""

    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_parent_directory(db_path: str) -> None:
    """Create the parent directory for ``db_path`` when it does not exist."""

    parent = os.path.dirname(db_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _ensure_columns(
    cursor: sqlite3.Cursor,
    table: str,
    column_definitions: dict[str, str],
) -> None:
    """Add missing columns to ``table`` using ``ALTER TABLE`` statements."""

    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1].upper() for row in cursor.fetchall()}
    for column_name, definition in column_definitions.items():
        if column_name.upper() not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_crm_schema(db_path: str) -> None:
    """Ensure the main CRM SQLite database contains the expected schema."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()

        # --- clients -----------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                DATE_SIGNATURE TEXT NOT NULL,
                CIVILITE_CLIENT TEXT DEFAULT '',
                NOM_CLIENT TEXT DEFAULT '',
                PRENOM_CLIENT TEXT DEFAULT '',
                TELEPHONE TEXT DEFAULT '',
                STATUT TEXT DEFAULT '',
                AGENT TEXT DEFAULT '',
                DEUXIEME_ADRESSE TEXT DEFAULT '',
                TROISIEME_ADRESSE TEXT DEFAULT '',
                TYPE_OFFRE TEXT DEFAULT '',
                CREE_PAR TEXT,
                MODIFIE_PAR TEXT,
                DATE_MODIF TEXT,
                campagne_id INTEGER DEFAULT 1
            )
            """
        )

        client_columns = {
            "CAMPAGNE_ID": "campagne_id INTEGER DEFAULT 1",
            "TITRE": "TITRE TEXT DEFAULT ''",
            "NOM_VENDEUR": "NOM_VENDEUR TEXT DEFAULT ''",
            "PRENOM_VENDEUR": "PRENOM_VENDEUR TEXT DEFAULT ''",
            "TELEPHONE_VENDEUR": "TELEPHONE_VENDEUR TEXT DEFAULT ''",
            "N_CONTRAT": "N_CONTRAT TEXT DEFAULT ''",
            "N_REFERENCE": "N_REFERENCE TEXT DEFAULT ''",
            "VALIDATION_PRODUIT1": "VALIDATION_PRODUIT1 TEXT DEFAULT ''",
            "STATUT_PRODUIT1": "STATUT_PRODUIT1 TEXT DEFAULT ''",
            "VALIDATION_PRODUIT2": "VALIDATION_PRODUIT2 TEXT DEFAULT ''",
            "STATUT_PRODUIT2": "STATUT_PRODUIT2 TEXT DEFAULT ''",
            "VALIDATION_PRODUIT3": "VALIDATION_PRODUIT3 TEXT DEFAULT ''",
            "STATUT_PRODUIT3": "STATUT_PRODUIT3 TEXT DEFAULT ''",
            "EXTRANET": "EXTRANET TEXT DEFAULT ''",
            "CALL_ID": "CALL_ID TEXT DEFAULT ''",
        }

        # Product specific columns for the VALANDRE campaign
        produits = [
            "STRATO",
            "LSR",
            "PRESSE",
            "ENI",
            "SERENITY",
            "PROTEC_ALLIANCE",
            "WEKIWI",
        ]
        for produit in produits:
            for suffix in ("NUM", "STATUT", "REMARQUE"):
                column = f"{produit}_{suffix}"
                client_columns[column] = f"{column} TEXT DEFAULT ''"

        _ensure_columns(cur, "clients", client_columns)

        # --- agents ------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                NOM TEXT NOT NULL UNIQUE,
                LOGIN TEXT NOT NULL UNIQUE,
                MDP TEXT NOT NULL,
                ROLE TEXT NOT NULL DEFAULT 'agent',
                campagne_id INTEGER DEFAULT 1,
                PAYS_CODE TEXT,
                photo TEXT,
                TV TEXT
            )
            """
        )
        agent_columns = {
            "PAYS_CODE": "PAYS_CODE TEXT",
            "PHOTO": "photo TEXT",
            "TV": "TV TEXT",
        }
        _ensure_columns(cur, "agents", agent_columns)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agents_tv ON agents(TV)")

        # --- journal_connexions -----------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_connexions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_nom TEXT NOT NULL,
                date_connexion TEXT NOT NULL,
                page TEXT,
                type_event TEXT DEFAULT 'connexion'
            )
            """
        )

        # --- historique_clients -----------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS historique_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                date_modif TEXT,
                agent TEXT,
                champ_modifie TEXT,
                ancienne_valeur TEXT,
                nouvelle_valeur TEXT
            )
            """
        )

        # --- campagnes --------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS campagnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                type_export TEXT NOT NULL
            )
            """
        )

        default_campaigns = (
            ("EXOSPHERE_SFR", "simple"),
            ("VALANDRE", "special"),
            ("HUMANITAIRE", "simple"),
        )
        cur.execute("SELECT nom FROM campagnes")
        existing_campaigns = {row[0] for row in cur.fetchall()}
        for nom, type_export in default_campaigns:
            if nom not in existing_campaigns:
                cur.execute(
                    "INSERT INTO campagnes(nom, type_export) VALUES (?, ?)",
                    (nom, type_export),
                )


def ensure_primes_table(db_path: str) -> None:
    """Ensure the ``primes_huma`` table exists in the CRM database."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS primes_huma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dons_cible INTEGER NOT NULL,
                don_moyen_cible INTEGER NOT NULL,
                prime_eur REAL NOT NULL,
                prime_dt REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_primes_cible
            ON primes_huma(dons_cible, don_moyen_cible)
            """
        )


def ensure_huma_schema(db_path: str) -> None:
    """Ensure the humanitarian SQLite database contains the expected schema."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base TEXT,
                agent TEXT,
                call_date TEXT,
                is_cu INTEGER DEFAULT 0,
                is_don INTEGER DEFAULT 0,
                is_donmail INTEGER DEFAULT 0,
                is_indecis INTEGER DEFAULT 0,
                montant REAL DEFAULT 0.0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS grh_hours (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jour TEXT,
                agent TEXT,
                heures REAL DEFAULT 0.0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS objectifs (
                agent TEXT PRIMARY KEY,
                OBJECTIF_DONS INTEGER,
                DON_MOYEN_CIBLE REAL,
                PRIME_BASE_EUR REAL
            )
            """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(call_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_base ON calls(base)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_grh_jour ON grh_hours(jour)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_grh_agent ON grh_hours(agent)")
