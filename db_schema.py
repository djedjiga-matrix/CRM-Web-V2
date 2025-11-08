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

from dataclasses import dataclass, field
from contextlib import contextmanager
import os
import sqlite3
from typing import Iterable, Mapping, Sequence


PRODUCT_COLUMNS = (
    "STRATO",
    "LSR",
    "PRESSE",
    "ENI",
    "SERENITY",
    "PROTEC_ALLIANCE",
    "WEKIWI",
)


__all__ = ["ensure_crm_schema", "ensure_primes_table", "ensure_huma_schema"]


@dataclass(frozen=True)
class SeedData:
    """Describe rows that must be present in a table."""

    columns: tuple[str, ...]
    rows: Sequence[tuple]
    unique_column: str

    @property
    def key_index(self) -> int:
        try:
            return self.columns.index(self.unique_column)
        except ValueError as exc:  # pragma: no cover - guarded by tests
            raise ValueError(
                f"La colonne unique '{self.unique_column}' est absente: {self.columns}"
            ) from exc


@dataclass(frozen=True)
class TableDefinition:
    """Hold the creation parameters of a SQLite table."""

    name: str
    create_sql: str
    column_definitions: Mapping[str, str] = field(default_factory=dict)
    indexes: Mapping[str, str] = field(default_factory=dict)
    seed_data: SeedData | None = None


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


def _ensure_indexes(
    cursor: sqlite3.Cursor,
    index_statements: Mapping[str, str],
) -> None:
    """Create each index in ``index_statements`` if it does not exist yet."""

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    existing = {row[0] for row in cursor.fetchall()}
    for index_name, statement in index_statements.items():
        if index_name not in existing:
            cursor.execute(statement)


def _seed_rows(cursor: sqlite3.Cursor, table: str, seed: SeedData) -> None:
    """Insert missing rows defined in ``seed`` while preserving existing data."""

    key_index = seed.key_index
    cursor.execute(f"SELECT {seed.unique_column} FROM {table}")
    existing = {row[0] for row in cursor.fetchall()}
    placeholders = ", ".join("?" for _ in seed.columns)
    columns = ", ".join(seed.columns)
    for row in seed.rows:
        if row[key_index] not in existing:
            cursor.execute(
                f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
                row,
            )
            existing.add(row[key_index])


def _apply_table_definition(cursor: sqlite3.Cursor, definition: TableDefinition) -> None:
    """Create a table, add columns, indexes and seed data in one place."""

    cursor.execute(definition.create_sql)
    if definition.column_definitions:
        _ensure_columns(cursor, definition.name, dict(definition.column_definitions))
    if definition.indexes:
        _ensure_indexes(cursor, definition.indexes)
    if definition.seed_data is not None:
        _seed_rows(cursor, definition.name, definition.seed_data)


DEFAULT_CAMPAIGNS = (
    ("EXOSPHERE_SFR", "simple"),
    ("VALANDRE", "special"),
    ("HUMANITAIRE", "simple"),
)


def _clients_definition() -> TableDefinition:
    client_columns: dict[str, str] = {
        "TELEPHONE": "TELEPHONE TEXT DEFAULT ''",
        "AGENT": "AGENT TEXT DEFAULT ''",
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
    for produit in PRODUCT_COLUMNS:
        for suffix in ("NUM", "STATUT", "REMARQUE"):
            column = f"{produit}_{suffix}"
            client_columns[column] = f"{column} TEXT DEFAULT ''"

    return TableDefinition(
        name="clients",
        create_sql="""
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
        """,
        column_definitions=client_columns,
        indexes={
            "idx_clients_telephone": "CREATE INDEX IF NOT EXISTS idx_clients_telephone ON clients(TELEPHONE)",
            "idx_clients_call_id": "CREATE INDEX IF NOT EXISTS idx_clients_call_id ON clients(CALL_ID)",
            "idx_clients_agent": "CREATE INDEX IF NOT EXISTS idx_clients_agent ON clients(AGENT)",
            "idx_clients_campagne": "CREATE INDEX IF NOT EXISTS idx_clients_campagne ON clients(campagne_id)",
        },
    )


def _agents_definition() -> TableDefinition:
    return TableDefinition(
        name="agents",
        create_sql="""
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
        """,
        column_definitions={
            "ROLE": "ROLE TEXT NOT NULL DEFAULT 'agent'",
            "PAYS_CODE": "PAYS_CODE TEXT",
            "PHOTO": "photo TEXT",
            "TV": "TV TEXT",
        },
        indexes={
            "idx_agents_tv": "CREATE INDEX IF NOT EXISTS idx_agents_tv ON agents(TV)",
            "idx_agents_role": "CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(ROLE)",
        },
    )


def _journal_definition() -> TableDefinition:
    return TableDefinition(
        name="journal_connexions",
        create_sql="""
            CREATE TABLE IF NOT EXISTS journal_connexions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_nom TEXT NOT NULL,
                date_connexion TEXT NOT NULL,
                page TEXT,
                type_event TEXT DEFAULT 'connexion'
            )
        """,
    )


def _historique_definition() -> TableDefinition:
    return TableDefinition(
        name="historique_clients",
        create_sql="""
            CREATE TABLE IF NOT EXISTS historique_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                date_modif TEXT,
                agent TEXT,
                champ_modifie TEXT,
                ancienne_valeur TEXT,
                nouvelle_valeur TEXT
            )
        """,
    )


def _campagnes_definition() -> TableDefinition:
    return TableDefinition(
        name="campagnes",
        create_sql="""
            CREATE TABLE IF NOT EXISTS campagnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                type_export TEXT NOT NULL
            )
        """,
        indexes={
            "idx_campagnes_nom": "CREATE UNIQUE INDEX IF NOT EXISTS idx_campagnes_nom ON campagnes(nom)",
        },
        seed_data=SeedData(
            columns=("nom", "type_export"),
            rows=DEFAULT_CAMPAIGNS,
            unique_column="nom",
        ),
    )


def ensure_crm_schema(db_path: str) -> None:
    """Ensure the main CRM SQLite database contains the expected schema."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()
        for definition in (
            _clients_definition(),
            _agents_definition(),
            _journal_definition(),
            _historique_definition(),
            _campagnes_definition(),
        ):
            _apply_table_definition(cur, definition)


def _primes_definition() -> TableDefinition:
    return TableDefinition(
        name="primes_huma",
        create_sql="""
            CREATE TABLE IF NOT EXISTS primes_huma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dons_cible INTEGER NOT NULL,
                don_moyen_cible INTEGER NOT NULL,
                prime_eur REAL NOT NULL,
                prime_dt REAL NOT NULL
            )
        """,
        indexes={
            "idx_primes_cible": "CREATE INDEX IF NOT EXISTS idx_primes_cible ON primes_huma(dons_cible, don_moyen_cible)",
        },
    )


def ensure_primes_table(db_path: str) -> None:
    """Ensure the ``primes_huma`` table exists in the CRM database."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()
        _apply_table_definition(cur, _primes_definition())


def _huma_definitions() -> tuple[TableDefinition, ...]:
    return (
        TableDefinition(
            name="calls",
            create_sql="""
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
            """,
            indexes={
                "idx_calls_date": "CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(call_date)",
                "idx_calls_agent": "CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent)",
                "idx_calls_base": "CREATE INDEX IF NOT EXISTS idx_calls_base ON calls(base)",
            },
        ),
        TableDefinition(
            name="grh_hours",
            create_sql="""
                CREATE TABLE IF NOT EXISTS grh_hours (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jour TEXT,
                    agent TEXT,
                    heures REAL DEFAULT 0.0
                )
            """,
            indexes={
                "idx_grh_jour": "CREATE INDEX IF NOT EXISTS idx_grh_jour ON grh_hours(jour)",
                "idx_grh_agent": "CREATE INDEX IF NOT EXISTS idx_grh_agent ON grh_hours(agent)",
            },
        ),
        TableDefinition(
            name="objectifs",
            create_sql="""
                CREATE TABLE IF NOT EXISTS objectifs (
                    agent TEXT PRIMARY KEY,
                    OBJECTIF_DONS INTEGER,
                    DON_MOYEN_CIBLE REAL,
                    PRIME_BASE_EUR REAL
                )
            """,
        ),
    )


def ensure_huma_schema(db_path: str) -> None:
    """Ensure the humanitarian SQLite database contains the expected schema."""

    _ensure_parent_directory(db_path)

    with _connect(db_path) as conn:
        cur = conn.cursor()
        for definition in _huma_definitions():
            _apply_table_definition(cur, definition)
