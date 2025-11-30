"""
AgentDAO pour CRM Web V2.

Ce module gère l'accès aux données des agents.
"""
import bcrypt
from typing import List, Dict, Optional
from app.models.base import BaseDAO
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AgentDAO(BaseDAO):
    """DAO pour la gestion des agents."""

    def __init__(self, db_path: str):
        """
        Initialise le DAO des agents.

        Args:
            db_path: Chemin vers la base de données
        """
        super().__init__(db_path)
        self.table = 'agents'

    def find_by_login(self, login: str) -> Optional[Dict]:
        """
        Trouve un agent par son login.

        Args:
            login: Login de l'agent

        Returns:
            Dictionnaire avec les données de l'agent ou None
        """
        query = f"SELECT * FROM {self.table} WHERE LOGIN = ?"
        return self.execute_query(query, (login,), fetch_one=True)

    def find_by_id(self, agent_id: int) -> Optional[Dict]:
        """
        Trouve un agent par son ID.

        Args:
            agent_id: ID de l'agent

        Returns:
            Dictionnaire avec les données de l'agent ou None
        """
        return super().find_by_id(self.table, agent_id)

    def find_all(
        self,
        campagne_id: Optional[int] = None,
        role: Optional[str] = None,
        order_by: str = 'NOM ASC'
    ) -> List[Dict]:
        """
        Trouve tous les agents avec filtres optionnels.

        Args:
            campagne_id: Filtrer par campagne
            role: Filtrer par rôle
            order_by: Clause ORDER BY

        Returns:
            Liste d'agents
        """
        where_clauses = []
        params = []

        if campagne_id is not None:
            where_clauses.append("campagne_id = ?")
            params.append(campagne_id)

        if role:
            where_clauses.append("ROLE = ?")
            params.append(role)

        where = " AND ".join(where_clauses) if where_clauses else None

        return super().find_all(
            self.table,
            where=where,
            params=tuple(params) if params else None,
            order_by=order_by
        )

    def authenticate(self, login: str, password: str) -> Optional[Dict]:
        """
        Authentifie un agent.

        Args:
            login: Login de l'agent
            password: Mot de passe en clair

        Returns:
            Dictionnaire avec les données de l'agent si authentification réussie, None sinon
        """
        agent = self.find_by_login(login)

        if not agent:
            logger.warning(f"Tentative de connexion avec login inexistant: {login}")
            return None

        # Vérifier le mot de passe
        if not agent.get('MDP'):
            logger.warning(f"Agent {login} sans mot de passe défini")
            return None

        try:
            # Vérifier le hash bcrypt
            if bcrypt.checkpw(password.encode('utf-8'), agent['MDP'].encode('utf-8')):
                logger.info(f"Authentification réussie pour: {login}")
                return agent
            else:
                logger.warning(f"Mot de passe incorrect pour: {login}")
                return None
        except (ValueError, AttributeError) as e:
            logger.error(f"Erreur lors de la vérification du mot de passe: {e}")
            return None

    def create(self, data: Dict) -> int:
        """
        Crée un nouvel agent.

        Args:
            data: Données de l'agent (doit contenir MDP en clair qui sera hashé)

        Returns:
            ID de l'agent créé
        """
        # Hasher le mot de passe si fourni
        if 'MDP' in data and data['MDP']:
            hashed = bcrypt.hashpw(data['MDP'].encode('utf-8'), bcrypt.gensalt())
            data['MDP'] = hashed.decode('utf-8')

        agent_id = self.insert(self.table, data)
        logger.info(f"Agent créé: {data.get('LOGIN')} (ID: {agent_id})")
        return agent_id

    def update_agent(self, agent_id: int, data: Dict) -> int:
        """
        Met à jour un agent.

        Args:
            agent_id: ID de l'agent
            data: Données à mettre à jour

        Returns:
            Nombre de lignes affectées
        """
        # Hasher le mot de passe si fourni
        if 'MDP' in data and data['MDP']:
            hashed = bcrypt.hashpw(data['MDP'].encode('utf-8'), bcrypt.gensalt())
            data['MDP'] = hashed.decode('utf-8')

        rows = self.update(self.table, data, "id = ?", (agent_id,))
        logger.info(f"Agent {agent_id} mis à jour")
        return rows

    def delete_agent(self, agent_id: int) -> int:
        """
        Supprime un agent.

        Args:
            agent_id: ID de l'agent

        Returns:
            Nombre de lignes supprimées
        """
        rows = self.delete(self.table, "id = ?", (agent_id,))
        logger.info(f"Agent {agent_id} supprimé")
        return rows

    def count_by_role(self, role: str) -> int:
        """
        Compte les agents par rôle.

        Args:
            role: Rôle à compter

        Returns:
            Nombre d'agents
        """
        return self.count(self.table, where="ROLE = ?", params=(role,))

    def get_agents_by_campagne(self, campagne_id: int) -> List[Dict]:
        """
        Récupère tous les agents d'une campagne.

        Args:
            campagne_id: ID de la campagne

        Returns:
            Liste des agents
        """
        return self.find_all(campagne_id=campagne_id)

    def login_exists(self, login: str, exclude_id: Optional[int] = None) -> bool:
        """
        Vérifie si un login existe déjà.

        Args:
            login: Login à vérifier
            exclude_id: ID d'agent à exclure (pour update)

        Returns:
            True si le login existe, False sinon
        """
        if exclude_id:
            query = f"SELECT id FROM {self.table} WHERE LOGIN = ? AND id != ?"
            result = self.execute_query(query, (login, exclude_id), fetch_one=True)
        else:
            query = f"SELECT id FROM {self.table} WHERE LOGIN = ?"
            result = self.execute_query(query, (login,), fetch_one=True)

        return result is not None
