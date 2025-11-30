"""
Modèles et DAO pour CRM Web V2.

Ce module fournit une couche d'abstraction pour l'accès aux données.
"""
from app.models.base import BaseDAO
from app.models.agent import AgentDAO
from app.models.client import ClientDAO
from app.models.journal import JournalDAO

__all__ = [
    'BaseDAO',
    'AgentDAO',
    'ClientDAO',
    'JournalDAO'
]
