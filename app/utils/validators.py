"""
Validateurs pour CRM Web V2.

Ce module fournit des fonctions de validation pour sécuriser les entrées utilisateur.
"""
import re
from datetime import datetime
from typing import Optional, List


def is_valid_phone(phone: str) -> bool:
    """
    Valide un numéro de téléphone.

    Args:
        phone: Numéro de téléphone à valider

    Returns:
        True si valide, False sinon
    """
    if not phone:
        return False
    # Accepte les numéros français et internationaux
    pattern = r'^[\d\s\-\+\(\)\.]{8,20}$'
    return bool(re.match(pattern, phone.strip()))


def is_valid_email(email: str) -> bool:
    """
    Valide une adresse email.

    Args:
        email: Adresse email à valider

    Returns:
        True si valide, False sinon
    """
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


def is_valid_date(date_str: str, formats: Optional[List[str]] = None) -> bool:
    """
    Valide une date selon différents formats.

    Args:
        date_str: Date à valider
        formats: Liste des formats acceptés (par défaut formats courants)

    Returns:
        True si valide, False sinon
    """
    if not date_str or not isinstance(date_str, str):
        return False

    if formats is None:
        formats = [
            '%Y-%m-%d',
            '%d/%m/%Y',
            '%d-%m-%Y',
            '%d.%m.%Y',
            '%m/%d/%Y'
        ]

    for fmt in formats:
        try:
            datetime.strptime(date_str.strip(), fmt)
            return True
        except (ValueError, TypeError):
            continue

    return False


def sanitize_table_name(table_name: str) -> Optional[str]:
    """
    Valide et sanitise un nom de table pour éviter les injections SQL.

    Args:
        table_name: Nom de table à valider

    Returns:
        Nom de table sanitisé ou None si invalide
    """
    # Whitelist des tables autorisées
    allowed_tables = {
        'clients',
        'clients_valandre',
        'agents',
        'campagnes',
        'journal_connexions',
        'historique_clients',
        'primes_huma',
        'calls',
        'grh_hours',
        'objectifs'
    }

    table_name = table_name.strip().lower()

    if table_name not in allowed_tables:
        return None

    return table_name


def sanitize_column_name(column_name: str) -> Optional[str]:
    """
    Valide un nom de colonne pour éviter les injections SQL.

    Args:
        column_name: Nom de colonne à valider

    Returns:
        Nom de colonne sanitisé ou None si invalide
    """
    # Accepte uniquement lettres, chiffres et underscores
    if not re.match(r'^[a-zA-Z0-9_]+$', column_name):
        return None

    return column_name


def sanitize_order_direction(direction: str) -> str:
    """
    Valide une direction de tri SQL.

    Args:
        direction: Direction de tri ('ASC' ou 'DESC')

    Returns:
        'ASC' ou 'DESC' (par défaut 'ASC' si invalide)
    """
    direction = direction.strip().upper()
    return 'DESC' if direction == 'DESC' else 'ASC'


def is_valid_file_extension(filename: str, allowed_extensions: set) -> bool:
    """
    Vérifie si l'extension du fichier est autorisée.

    Args:
        filename: Nom du fichier
        allowed_extensions: Set des extensions autorisées

    Returns:
        True si l'extension est autorisée, False sinon
    """
    if not filename or '.' not in filename:
        return False

    extension = filename.rsplit('.', 1)[1].lower()
    return extension in allowed_extensions


def sanitize_filename(filename: str) -> str:
    """
    Sanitise un nom de fichier pour éviter les problèmes de sécurité.

    Args:
        filename: Nom du fichier

    Returns:
        Nom de fichier sanitisé
    """
    # Supprimer les caractères dangereux
    filename = re.sub(r'[^\w\s\-\.]', '', filename)
    # Supprimer les doubles espaces
    filename = re.sub(r'\s+', '_', filename)
    # Limiter la longueur
    return filename[:255]


def is_valid_statut(statut: str) -> bool:
    """
    Valide un statut client.

    Args:
        statut: Statut à valider

    Returns:
        True si valide, False sinon
    """
    valid_statuts = {
        'valide', 'validé', 'validée',
        'non valide', 'non validé', 'refusé',
        'en attente', 'en cours', 'annulé', 'annulée'
    }
    return statut.lower().strip() in valid_statuts if statut else False


def is_valid_role(role: str) -> bool:
    """
    Valide un rôle utilisateur.

    Args:
        role: Rôle à valider

    Returns:
        True si valide, False sinon
    """
    valid_roles = {'admin', 'superviseur', 'agent'}
    return role.lower().strip() in valid_roles if role else False
