"""
Décorateurs pour CRM Web V2.

Ce module fournit des décorateurs pour l'authentification et l'autorisation.
"""
from functools import wraps
from flask import session, redirect, url_for, flash, abort
from typing import Callable, List, Optional


def login_required(f: Callable) -> Callable:
    """
    Décorateur pour protéger les routes nécessitant une authentification.

    Usage:
        @app.route('/protected')
        @login_required
        def protected_route():
            return "Page protégée"
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'agent_nom' not in session:
            flash('Veuillez vous connecter pour accéder à cette page.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(roles: List[str]) -> Callable:
    """
    Décorateur pour protéger les routes nécessitant un rôle spécifique.

    Args:
        roles: Liste des rôles autorisés (ex: ['admin', 'superviseur'])

    Usage:
        @app.route('/admin')
        @role_required(['admin'])
        def admin_route():
            return "Page admin"

        @app.route('/management')
        @role_required(['admin', 'superviseur'])
        def management_route():
            return "Page gestion"
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            user_role = session.get('agent_role', '').lower()
            if user_role not in [role.lower() for role in roles]:
                flash(f"Accès refusé. Rôle requis: {', '.join(roles)}", 'danger')
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f: Callable) -> Callable:
    """
    Décorateur pour protéger les routes réservées aux administrateurs.

    Usage:
        @app.route('/admin/sensitive')
        @admin_required
        def sensitive_admin_route():
            return "Page admin sensible"
    """
    return role_required(['admin'])(f)


def admin_or_superviseur_required(f: Callable) -> Callable:
    """
    Décorateur pour protéger les routes réservées aux admins et superviseurs.

    Usage:
        @app.route('/management/team')
        @admin_or_superviseur_required
        def team_management():
            return "Gestion d'équipe"
    """
    return role_required(['admin', 'superviseur'])(f)
