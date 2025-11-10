"""Tests pour les utilitaires de schéma SQLite du CRM."""

import sqlite3
from pathlib import Path

import pytest

from db_schema import ensure_crm_schema, ensure_primes_table, ensure_huma_schema


def _table_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA index_list('{table}')")
    return {row[1] for row in cur.fetchall()}


@pytest.mark.parametrize("ensure_fn", [ensure_crm_schema, ensure_primes_table, ensure_huma_schema])
def test_helpers_create_parent_directory(tmp_path: Path, ensure_fn) -> None:
    """Chaque helper doit créer le dossier parent si nécessaire."""

    db_path = tmp_path / "nested" / "schema.db"
    ensure_fn(str(db_path))
    assert db_path.exists(), "Le fichier SQLite doit être créé automatiquement."


def test_ensure_crm_schema_creates_expected_objects(tmp_path: Path) -> None:
    db_path = tmp_path / "crm.db"
    ensure_crm_schema(str(db_path))
    ensure_crm_schema(str(db_path))  # idempotent

    with sqlite3.connect(db_path) as conn:
        tables = _table_names(conn)
        expected_tables = {
            "clients",
            "agents",
            "journal_connexions",
            "historique_clients",
            "campagnes",
        }
        assert expected_tables.issubset(tables)

        client_columns = _column_names(conn, "clients")
        for column in {
            "campagne_id",
            "CALL_ID",
            "N_REFERENCE",
            "STRATO_NUM",
            "STRATO_STATUT",
            "STRATO_REMARQUE",
        }:
            assert column in client_columns

        agent_columns = _column_names(conn, "agents")
        for column in {"PAYS_CODE", "photo", "TV"}:
            assert column in agent_columns

        client_indexes = _index_names(conn, "clients")
        assert {
            "idx_clients_telephone",
            "idx_clients_call_id",
            "idx_clients_agent",
            "idx_clients_campagne",
        }.issubset(client_indexes)

        agent_indexes = _index_names(conn, "agents")
        assert {"idx_agents_tv", "idx_agents_role"}.issubset(agent_indexes)

        cur = conn.execute("SELECT nom, type_export FROM campagnes")
        campaigns = cur.fetchall()
        assert len(campaigns) == 3
        assert {row[0] for row in campaigns} == {
            "EXOSPHERE_SFR",
            "VALANDRE",
            "HUMANITAIRE",
        }

        campagne_indexes = _index_names(conn, "campagnes")
        assert "idx_campagnes_nom" in campagne_indexes


def test_ensure_crm_schema_adds_missing_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY,
                DATE_SIGNATURE TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY,
                NOM TEXT
            )
            """
        )

    ensure_crm_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        client_columns = _column_names(conn, "clients")
        assert {"campagne_id", "CALL_ID", "WEKIWI_STATUT"}.issubset(client_columns)

        agent_columns = _column_names(conn, "agents")
        assert {"PAYS_CODE", "photo", "TV"}.issubset(agent_columns)


def test_ensure_primes_table_creates_table_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "crm.db"
    ensure_primes_table(str(db_path))
    ensure_primes_table(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = _table_names(conn)
        assert "primes_huma" in tables
        indexes = _index_names(conn, "primes_huma")
        assert "idx_primes_cible" in indexes


def test_ensure_crm_schema_deduplicates_campaigns(tmp_path: Path) -> None:
    db_path = tmp_path / "crm.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE campagnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                type_export TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO campagnes(nom, type_export) VALUES (?, ?)",
            [
                ("  EXOSPHERE_SFR  ", "simple"),
                ("EXOSPHERE_SFR", "special"),
                ("VALANDRE", None),
            ],
        )

    ensure_crm_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT nom, type_export FROM campagnes ORDER BY nom"
        )
        rows = cur.fetchall()
        assert rows == [
            ("EXOSPHERE_SFR", "simple"),
            ("HUMANITAIRE", "simple"),
            ("VALANDRE", "simple"),
        ]

        indexes = _index_names(conn, "campagnes")
        assert "idx_campagnes_nom" in indexes


def test_ensure_huma_schema_creates_tables_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "huma.db"
    ensure_huma_schema(str(db_path))
    ensure_huma_schema(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = _table_names(conn)
        assert {"calls", "grh_hours", "objectifs"}.issubset(tables)

        assert "idx_calls_date" in _index_names(conn, "calls")
        assert "idx_calls_agent" in _index_names(conn, "calls")
        assert "idx_calls_base" in _index_names(conn, "calls")
        assert "idx_grh_jour" in _index_names(conn, "grh_hours")
        assert "idx_grh_agent" in _index_names(conn, "grh_hours")
