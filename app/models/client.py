"""
ClientDAO pour CRM Web V2.

Ce module gère l'accès aux données des clients.
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from app.models.base import BaseDAO
from app.utils.logger import get_logger
from app.utils.validators import sanitize_table_name, sanitize_column_name, sanitize_order_direction

logger = get_logger(__name__)


class ClientDAO(BaseDAO):
    """DAO pour la gestion des clients."""

    # Tables supportées
    TABLE_CLIENTS = 'clients'
    TABLE_CLIENTS_VALANDRE = 'clients_valandre'

    def __init__(self, db_path: str):
        """
        Initialise le DAO des clients.

        Args:
            db_path: Chemin vers la base de données
        """
        super().__init__(db_path)

    def _validate_table(self, table: str) -> str:
        """
        Valide le nom de table pour éviter les injections SQL.

        Args:
            table: Nom de table à valider

        Returns:
            Nom de table validé

        Raises:
            ValueError: Si le nom de table est invalide
        """
        validated = sanitize_table_name(table)
        if validated not in [self.TABLE_CLIENTS, self.TABLE_CLIENTS_VALANDRE]:
            raise ValueError(f"Table invalide: {table}")
        return validated

    def find_by_id(self, client_id: int, table: str = TABLE_CLIENTS) -> Optional[Dict]:
        """
        Trouve un client par son ID.

        Args:
            client_id: ID du client
            table: Table à interroger

        Returns:
            Dictionnaire avec les données du client ou None
        """
        table = self._validate_table(table)
        return super().find_by_id(table, client_id)

    def find_all(
        self,
        table: str = TABLE_CLIENTS,
        campagne_id: Optional[int] = None,
        agent: Optional[str] = None,
        statut: Optional[str] = None,
        search: Optional[str] = None,
        order_by: str = 'DATE_MODIF DESC',
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict]:
        """
        Trouve tous les clients avec filtres optionnels.

        Args:
            table: Table à interroger
            campagne_id: Filtrer par campagne
            agent: Filtrer par agent
            statut: Filtrer par statut
            search: Recherche sur nom/téléphone
            order_by: Clause ORDER BY
            limit: Limite de résultats
            offset: Décalage pour pagination

        Returns:
            Liste de clients
        """
        table = self._validate_table(table)

        where_clauses = []
        params = []

        if campagne_id is not None:
            where_clauses.append("campagne_id = ?")
            params.append(campagne_id)

        if agent:
            where_clauses.append("AGENT = ?")
            params.append(agent)

        if statut:
            where_clauses.append("STATUT = ?")
            params.append(statut)

        if search:
            where_clauses.append(
                "(NOM_CLIENT LIKE ? OR PRENOM_CLIENT LIKE ? OR TELEPHONE LIKE ?)"
            )
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        where = " AND ".join(where_clauses) if where_clauses else None

        return super().find_all(
            table,
            where=where,
            params=tuple(params) if params else None,
            order_by=order_by,
            limit=limit,
            offset=offset
        )

    def count_clients(
        self,
        table: str = TABLE_CLIENTS,
        campagne_id: Optional[int] = None,
        agent: Optional[str] = None,
        statut: Optional[str] = None,
        search: Optional[str] = None
    ) -> int:
        """
        Compte les clients avec filtres optionnels.

        Args:
            table: Table à interroger
            campagne_id: Filtrer par campagne
            agent: Filtrer par agent
            statut: Filtrer par statut
            search: Recherche sur nom/téléphone

        Returns:
            Nombre de clients
        """
        table = self._validate_table(table)

        where_clauses = []
        params = []

        if campagne_id is not None:
            where_clauses.append("campagne_id = ?")
            params.append(campagne_id)

        if agent:
            where_clauses.append("AGENT = ?")
            params.append(agent)

        if statut:
            where_clauses.append("STATUT = ?")
            params.append(statut)

        if search:
            where_clauses.append(
                "(NOM_CLIENT LIKE ? OR PRENOM_CLIENT LIKE ? OR TELEPHONE LIKE ?)"
            )
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        where = " AND ".join(where_clauses) if where_clauses else None

        return self.count(table, where=where, params=tuple(params) if params else None)

    def create(self, data: Dict, table: str = TABLE_CLIENTS) -> int:
        """
        Crée un nouveau client.

        Args:
            data: Données du client
            table: Table cible

        Returns:
            ID du client créé
        """
        table = self._validate_table(table)

        # Ajouter les métadonnées
        if 'DATE_MODIF' not in data:
            data['DATE_MODIF'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        client_id = self.insert(table, data)
        logger.info(f"Client créé: {data.get('NOM_CLIENT')} {data.get('PRENOM_CLIENT')} (ID: {client_id})")
        return client_id

    def update_client(
        self,
        client_id: int,
        data: Dict,
        table: str = TABLE_CLIENTS
    ) -> int:
        """
        Met à jour un client.

        Args:
            client_id: ID du client
            data: Données à mettre à jour
            table: Table cible

        Returns:
            Nombre de lignes affectées
        """
        table = self._validate_table(table)

        # Mettre à jour la date de modification
        if 'DATE_MODIF' not in data:
            data['DATE_MODIF'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rows = self.update(table, data, "id = ?", (client_id,))
        logger.info(f"Client {client_id} mis à jour dans {table}")
        return rows

    def delete_client(self, client_id: int, table: str = TABLE_CLIENTS) -> int:
        """
        Supprime un client.

        Args:
            client_id: ID du client
            table: Table cible

        Returns:
            Nombre de lignes supprimées
        """
        table = self._validate_table(table)

        rows = self.delete(table, "id = ?", (client_id,))
        logger.info(f"Client {client_id} supprimé de {table}")
        return rows

    def find_by_phone(self, phone: str, table: str = TABLE_CLIENTS) -> Optional[Dict]:
        """
        Trouve un client par numéro de téléphone.

        Args:
            phone: Numéro de téléphone
            table: Table à interroger

        Returns:
            Client ou None
        """
        table = self._validate_table(table)
        query = f"SELECT * FROM {table} WHERE TELEPHONE = ?"
        return self.execute_query(query, (phone,), fetch_one=True)

    def find_by_contract(self, contract: str, table: str = TABLE_CLIENTS) -> Optional[Dict]:
        """
        Trouve un client par numéro de contrat.

        Args:
            contract: Numéro de contrat
            table: Table à interroger

        Returns:
            Client ou None
        """
        table = self._validate_table(table)
        query = f"SELECT * FROM {table} WHERE N_CONTRAT = ?"
        return self.execute_query(query, (contract,), fetch_one=True)

    def get_statistics(self, table: str = TABLE_CLIENTS, campagne_id: Optional[int] = None) -> Dict:
        """
        Récupère les statistiques des clients.

        Args:
            table: Table à interroger
            campagne_id: Filtrer par campagne

        Returns:
            Dictionnaire avec les statistiques
        """
        table = self._validate_table(table)

        where_clause = "WHERE campagne_id = ?" if campagne_id else ""
        params = (campagne_id,) if campagne_id else None

        query = f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN STATUT LIKE '%valide%' OR STATUT LIKE '%validé%' THEN 1 ELSE 0 END) as valides,
                SUM(CASE WHEN STATUT LIKE '%non valide%' OR STATUT LIKE '%refusé%' THEN 1 ELSE 0 END) as non_valides,
                SUM(CASE WHEN STATUT LIKE '%en attente%' OR STATUT LIKE '%en cours%' THEN 1 ELSE 0 END) as en_attente,
                COUNT(DISTINCT AGENT) as agents_actifs
            FROM {table}
            {where_clause}
        """

        result = self.execute_query(query, params, fetch_one=True)
        return result or {}

    def get_clients_by_agent(
        self,
        agent: str,
        table: str = TABLE_CLIENTS,
        statut: Optional[str] = None
    ) -> List[Dict]:
        """
        Récupère les clients d'un agent.

        Args:
            agent: Nom de l'agent
            table: Table à interroger
            statut: Filtrer par statut

        Returns:
            Liste des clients
        """
        return self.find_all(table=table, agent=agent, statut=statut)

    def detect_table_for_client(self, client_id: int) -> Optional[str]:
        """
        Détecte dans quelle table se trouve un client.

        Args:
            client_id: ID du client

        Returns:
            Nom de la table ou None
        """
        # Vérifier dans clients
        if self.find_by_id(client_id, self.TABLE_CLIENTS):
            return self.TABLE_CLIENTS

        # Vérifier dans clients_valandre
        if self.find_by_id(client_id, self.TABLE_CLIENTS_VALANDRE):
            return self.TABLE_CLIENTS_VALANDRE

        return None
