"""
Fonctions utilitaires pour CRM Web V2.

Ce module fournit des fonctions helpers génériques.
"""
import math
from datetime import datetime
from typing import Optional, Any, Dict, List
import pandas as pd


def parse_date_safe(date_str: str) -> Optional[str]:
    """
    Parse une date de manière sécurisée selon plusieurs formats.

    Args:
        date_str: Date à parser

    Returns:
        Date au format ISO (YYYY-MM-DD) ou None si invalide
    """
    if not date_str or pd.isna(date_str):
        return None

    # Si c'est déjà un datetime
    if isinstance(date_str, datetime):
        return date_str.strftime('%Y-%m-%d')

    # Formats supportés
    formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%d.%m.%Y',
        '%m/%d/%Y',
        '%Y/%m/%d'
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(str(date_str).strip(), fmt)
            return dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue

    return None


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalise un numéro de téléphone.

    Args:
        phone: Numéro de téléphone

    Returns:
        Numéro normalisé ou None
    """
    if not phone or pd.isna(phone):
        return None

    # Supprimer tous les caractères non numériques sauf +
    phone = str(phone).strip()
    normalized = ''.join(c for c in phone if c.isdigit() or c == '+')

    return normalized if normalized else None


def normalize_statut(statut: str) -> str:
    """
    Normalise un statut client.

    Args:
        statut: Statut à normaliser

    Returns:
        Statut normalisé
    """
    if not statut:
        return ''

    statut = str(statut).strip().lower()

    # Mapping des variantes
    valides = ['valide', 'validé', 'validée', 'ok']
    non_valides = ['non valide', 'non validé', 'refusé', 'refusée', 'ko']

    if any(v in statut for v in valides):
        return 'valide'
    elif any(nv in statut for nv in non_valides):
        return 'non valide'

    return statut


def nan_to_empty(value: Any) -> Any:
    """
    Convertit NaN en chaîne vide pour l'affichage.

    Args:
        value: Valeur à convertir

    Returns:
        Valeur ou chaîne vide
    """
    if pd.isna(value) or value is None:
        return ''
    return value


def calculate_pagination(total_items: int, page: int, per_page: int) -> Dict[str, Any]:
    """
    Calcule les informations de pagination.

    Args:
        total_items: Nombre total d'éléments
        page: Page actuelle (commence à 1)
        per_page: Éléments par page

    Returns:
        Dictionnaire avec les infos de pagination
    """
    total_pages = math.ceil(total_items / per_page) if per_page > 0 else 1
    page = max(1, min(page, total_pages))  # Clamp entre 1 et total_pages

    offset = (page - 1) * per_page
    has_prev = page > 1
    has_next = page < total_pages

    return {
        'page': page,
        'per_page': per_page,
        'total_items': total_items,
        'total_pages': total_pages,
        'offset': offset,
        'limit': per_page,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None
    }


def format_currency(amount: float, currency: str = 'EUR', locale: str = 'fr_FR') -> str:
    """
    Formate un montant en devise.

    Args:
        amount: Montant
        currency: Code devise (EUR, DT, etc.)
        locale: Locale (fr_FR, en_US, etc.)

    Returns:
        Montant formaté
    """
    try:
        if pd.isna(amount) or amount is None:
            return ''

        amount = float(amount)

        if currency == 'DT':
            # Format tunisien
            return f"{amount:,.2f} DT".replace(',', ' ')
        elif currency == 'EUR':
            # Format européen
            return f"{amount:,.2f} €".replace(',', ' ')
        else:
            return f"{amount:,.2f} {currency}".replace(',', ' ')
    except (ValueError, TypeError):
        return str(amount) if amount else ''


def get_client_status_badge_class(statut: str) -> str:
    """
    Retourne la classe CSS pour le badge de statut.

    Args:
        statut: Statut du client

    Returns:
        Classe CSS Tailwind
    """
    statut = str(statut).lower() if statut else ''

    if 'valide' in statut or 'validé' in statut:
        return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
    elif 'non valide' in statut or 'refusé' in statut:
        return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
    elif 'en attente' in statut or 'en cours' in statut:
        return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200'
    else:
        return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200'


def safe_int(value: Any, default: int = 0) -> int:
    """
    Convertit une valeur en int de manière sécurisée.

    Args:
        value: Valeur à convertir
        default: Valeur par défaut

    Returns:
        Entier ou valeur par défaut
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Convertit une valeur en float de manière sécurisée.

    Args:
        value: Valeur à convertir
        default: Valeur par défaut

    Returns:
        Float ou valeur par défaut
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def dict_factory(cursor, row) -> Dict:
    """
    Factory pour convertir les résultats SQLite en dictionnaires.

    Args:
        cursor: Curseur SQLite
        row: Ligne de résultat

    Returns:
        Dictionnaire avec les noms de colonnes comme clés
    """
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d
