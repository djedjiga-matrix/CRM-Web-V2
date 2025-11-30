"""
BaseDAO pour CRM Web V2.

Ce module fournit la classe de base pour tous les DAO avec gestion sécurisée
des connexions et requêtes préparées pour éviter les injections SQL.
"""
import sqlite3
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Tuple
from app.utils.logger import get_logger
from app.utils.helpers import dict_factory

logger = get_logger(__name__)


class BaseDAO:
    """
    Classe de base pour tous les DAO.

    Fournit des méthodes pour gérer les connexions de manière sécurisée
    et exécuter des requêtes SQL avec des requêtes préparées.
    """

    def __init__(self, db_path: str):
        """
        Initialise le DAO.

        Args:
            db_path: Chemin vers la base de données SQLite
        """
        self.db_path = db_path

    @contextmanager
    def get_connection(self):
        """
        Context manager pour gérer les connexions de manière sécurisée.

        Yields:
            Connection SQLite

        Usage:
            with dao.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM table")
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = dict_factory  # Retourner des dictionnaires
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            if conn:
                conn.rollback()
            logger.error(f"Erreur base de données: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch_one: bool = False,
        fetch_all: bool = False
    ) -> Any:
        """
        Exécute une requête SQL de manière sécurisée.

        Args:
            query: Requête SQL avec placeholders (?)
            params: Tuple de paramètres pour la requête
            fetch_one: Si True, retourne une seule ligne
            fetch_all: Si True, retourne toutes les lignes

        Returns:
            Résultat de la requête ou None

        Raises:
            sqlite3.Error: En cas d'erreur SQL
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if fetch_one:
                return cursor.fetchone()
            elif fetch_all:
                return cursor.fetchall()
            else:
                return cursor.lastrowid

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """
        Exécute une requête SQL plusieurs fois (batch insert/update).

        Args:
            query: Requête SQL avec placeholders (?)
            params_list: Liste de tuples de paramètres

        Returns:
            Nombre de lignes affectées

        Raises:
            sqlite3.Error: En cas d'erreur SQL
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, params_list)
            return cursor.rowcount

    def find_by_id(self, table: str, id_value: int, id_column: str = 'id') -> Optional[Dict]:
        """
        Trouve un enregistrement par ID.

        Args:
            table: Nom de la table
            id_value: Valeur de l'ID
            id_column: Nom de la colonne ID

        Returns:
            Dictionnaire avec les données ou None
        """
        query = f"SELECT * FROM {table} WHERE {id_column} = ?"
        return self.execute_query(query, (id_value,), fetch_one=True)

    def find_all(
        self,
        table: str,
        where: Optional[str] = None,
        params: Optional[Tuple] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict]:
        """
        Trouve tous les enregistrements avec filtres optionnels.

        Args:
            table: Nom de la table
            where: Clause WHERE (sans le WHERE)
            params: Paramètres pour la clause WHERE
            order_by: Clause ORDER BY (sans le ORDER BY)
            limit: Limite de résultats
            offset: Décalage pour pagination

        Returns:
            Liste de dictionnaires
        """
        query = f"SELECT * FROM {table}"

        if where:
            query += f" WHERE {where}"

        if order_by:
            query += f" ORDER BY {order_by}"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        return self.execute_query(query, params, fetch_all=True)

    def count(
        self,
        table: str,
        where: Optional[str] = None,
        params: Optional[Tuple] = None
    ) -> int:
        """
        Compte les enregistrements.

        Args:
            table: Nom de la table
            where: Clause WHERE (sans le WHERE)
            params: Paramètres pour la clause WHERE

        Returns:
            Nombre d'enregistrements
        """
        query = f"SELECT COUNT(*) as count FROM {table}"

        if where:
            query += f" WHERE {where}"

        result = self.execute_query(query, params, fetch_one=True)
        return result['count'] if result else 0

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """
        Insère un enregistrement.

        Args:
            table: Nom de la table
            data: Dictionnaire des données à insérer

        Returns:
            ID de l'enregistrement inséré
        """
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        return self.execute_query(query, tuple(data.values()))

    def update(
        self,
        table: str,
        data: Dict[str, Any],
        where: str,
        params: Tuple
    ) -> int:
        """
        Met à jour des enregistrements.

        Args:
            table: Nom de la table
            data: Dictionnaire des données à mettre à jour
            where: Clause WHERE (sans le WHERE)
            params: Paramètres pour la clause WHERE

        Returns:
            Nombre de lignes affectées
        """
        set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"

        all_params = tuple(data.values()) + params

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, all_params)
            return cursor.rowcount

    def delete(self, table: str, where: str, params: Tuple) -> int:
        """
        Supprime des enregistrements.

        Args:
            table: Nom de la table
            where: Clause WHERE (sans le WHERE)
            params: Paramètres pour la clause WHERE

        Returns:
            Nombre de lignes supprimées
        """
        query = f"DELETE FROM {table} WHERE {where}"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.rowcount

    def table_exists(self, table_name: str) -> bool:
        """
        Vérifie si une table existe.

        Args:
            table_name: Nom de la table

        Returns:
            True si la table existe, False sinon
        """
        query = """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name=?
        """
        result = self.execute_query(query, (table_name,), fetch_one=True)
        return result is not None
