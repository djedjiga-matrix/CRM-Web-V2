"""
JournalDAO pour CRM Web V2.

Ce module gère l'accès aux données du journal et de l'historique.
"""
from typing import List, Dict, Optional
from datetime import datetime
from app.models.base import BaseDAO
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JournalDAO(BaseDAO):
    """DAO pour la gestion du journal et de l'historique."""

    TABLE_JOURNAL = 'journal_connexions'
    TABLE_HISTORIQUE = 'historique_clients'

    def __init__(self, db_path: str):
        """
        Initialise le DAO du journal.

        Args:
            db_path: Chemin vers la base de données
        """
        super().__init__(db_path)

    # ===== Journal des connexions =====

    def log_connexion(
        self,
        agent_nom: str,
        page: str,
        type_event: str = 'connexion'
    ) -> int:
        """
        Enregistre une connexion dans le journal.

        Args:
            agent_nom: Nom de l'agent
            page: Page visitée
            type_event: Type d'événement

        Returns:
            ID de l'enregistrement créé
        """
        data = {
            'agent_nom': agent_nom,
            'date_connexion': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'page': page,
            'type_event': type_event
        }

        log_id = self.insert(self.TABLE_JOURNAL, data)
        logger.debug(f"Connexion enregistrée: {agent_nom} - {page} - {type_event}")
        return log_id

    def get_connexions(
        self,
        agent_nom: Optional[str] = None,
        date_debut: Optional[str] = None,
        date_fin: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """
        Récupère les connexions du journal.

        Args:
            agent_nom: Filtrer par agent
            date_debut: Date de début (YYYY-MM-DD)
            date_fin: Date de fin (YYYY-MM-DD)
            limit: Limite de résultats
            offset: Décalage pour pagination

        Returns:
            Liste des connexions
        """
        where_clauses = []
        params = []

        if agent_nom:
            where_clauses.append("agent_nom = ?")
            params.append(agent_nom)

        if date_debut:
            where_clauses.append("date_connexion >= ?")
            params.append(date_debut)

        if date_fin:
            where_clauses.append("date_connexion <= ?")
            params.append(date_fin + ' 23:59:59')

        where = " AND ".join(where_clauses) if where_clauses else None

        return self.find_all(
            self.TABLE_JOURNAL,
            where=where,
            params=tuple(params) if params else None,
            order_by='date_connexion DESC',
            limit=limit,
            offset=offset
        )

    def count_connexions(
        self,
        agent_nom: Optional[str] = None,
        date_debut: Optional[str] = None,
        date_fin: Optional[str] = None
    ) -> int:
        """
        Compte les connexions.

        Args:
            agent_nom: Filtrer par agent
            date_debut: Date de début
            date_fin: Date de fin

        Returns:
            Nombre de connexions
        """
        where_clauses = []
        params = []

        if agent_nom:
            where_clauses.append("agent_nom = ?")
            params.append(agent_nom)

        if date_debut:
            where_clauses.append("date_connexion >= ?")
            params.append(date_debut)

        if date_fin:
            where_clauses.append("date_connexion <= ?")
            params.append(date_fin + ' 23:59:59')

        where = " AND ".join(where_clauses) if where_clauses else None

        return self.count(
            self.TABLE_JOURNAL,
            where=where,
            params=tuple(params) if params else None
        )

    def get_presence_stats(self, date: str) -> List[Dict]:
        """
        Récupère les statistiques de présence pour une date.

        Args:
            date: Date (YYYY-MM-DD)

        Returns:
            Liste des statistiques par agent
        """
        query = f"""
            SELECT
                agent_nom,
                COUNT(*) as nb_actions,
                MIN(date_connexion) as premiere_connexion,
                MAX(date_connexion) as derniere_connexion
            FROM {self.TABLE_JOURNAL}
            WHERE DATE(date_connexion) = ?
            GROUP BY agent_nom
            ORDER BY nb_actions DESC
        """

        return self.execute_query(query, (date,), fetch_all=True)

    # ===== Historique des modifications clients =====

    def log_modification(
        self,
        client_id: int,
        agent: str,
        champ_modifie: str,
        ancienne_valeur: str,
        nouvelle_valeur: str
    ) -> int:
        """
        Enregistre une modification de client dans l'historique.

        Args:
            client_id: ID du client modifié
            agent: Nom de l'agent
            champ_modifie: Nom du champ modifié
            ancienne_valeur: Ancienne valeur
            nouvelle_valeur: Nouvelle valeur

        Returns:
            ID de l'enregistrement créé
        """
        data = {
            'client_id': client_id,
            'date_modif': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'agent': agent,
            'champ_modifie': champ_modifie,
            'ancienne_valeur': str(ancienne_valeur) if ancienne_valeur else '',
            'nouvelle_valeur': str(nouvelle_valeur) if nouvelle_valeur else ''
        }

        hist_id = self.insert(self.TABLE_HISTORIQUE, data)
        logger.debug(f"Modification enregistrée: client {client_id} - {champ_modifie}")
        return hist_id

    def log_modifications_batch(
        self,
        client_id: int,
        agent: str,
        modifications: Dict[str, tuple]
    ) -> int:
        """
        Enregistre plusieurs modifications en batch.

        Args:
            client_id: ID du client
            agent: Nom de l'agent
            modifications: Dict {champ: (ancienne_valeur, nouvelle_valeur)}

        Returns:
            Nombre de modifications enregistrées
        """
        count = 0
        for champ, (ancienne, nouvelle) in modifications.items():
            if ancienne != nouvelle:  # Seulement si réellement modifié
                self.log_modification(client_id, agent, champ, ancienne, nouvelle)
                count += 1

        return count

    def get_historique_client(
        self,
        client_id: int,
        limit: int = 100
    ) -> List[Dict]:
        """
        Récupère l'historique des modifications d'un client.

        Args:
            client_id: ID du client
            limit: Limite de résultats

        Returns:
            Liste des modifications
        """
        return self.find_all(
            self.TABLE_HISTORIQUE,
            where="client_id = ?",
            params=(client_id,),
            order_by='date_modif DESC',
            limit=limit
        )

    def get_historique_agent(
        self,
        agent: str,
        date_debut: Optional[str] = None,
        date_fin: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Récupère l'historique des modifications d'un agent.

        Args:
            agent: Nom de l'agent
            date_debut: Date de début
            date_fin: Date de fin
            limit: Limite de résultats

        Returns:
            Liste des modifications
        """
        where_clauses = ["agent = ?"]
        params = [agent]

        if date_debut:
            where_clauses.append("date_modif >= ?")
            params.append(date_debut)

        if date_fin:
            where_clauses.append("date_modif <= ?")
            params.append(date_fin + ' 23:59:59')

        where = " AND ".join(where_clauses)

        return self.find_all(
            self.TABLE_HISTORIQUE,
            where=where,
            params=tuple(params),
            order_by='date_modif DESC',
            limit=limit
        )

    def count_modifications_periode(
        self,
        date_debut: str,
        date_fin: str,
        agent: Optional[str] = None
    ) -> int:
        """
        Compte les modifications sur une période.

        Args:
            date_debut: Date de début
            date_fin: Date de fin
            agent: Filtrer par agent

        Returns:
            Nombre de modifications
        """
        where_clauses = [
            "date_modif >= ?",
            "date_modif <= ?"
        ]
        params = [date_debut, date_fin + ' 23:59:59']

        if agent:
            where_clauses.append("agent = ?")
            params.append(agent)

        where = " AND ".join(where_clauses)

        return self.count(
            self.TABLE_HISTORIQUE,
            where=where,
            params=tuple(params)
        )
