"""
Application Factory pour CRM Web V2.

Ce module crée et configure l'application Flask selon le pattern Factory.
"""
import os
from pathlib import Path
from flask import Flask
from app.config import get_config
from app.extensions import socketio, limiter
from app.utils.logger import setup_logging


def create_app(config_name: str = None) -> Flask:
    """
    Factory pour créer l'application Flask.

    Args:
        config_name: Nom de la configuration (development, production, testing)

    Returns:
        Instance Flask configurée
    """
    # Créer l'instance Flask
    app = Flask(
        __name__,
        template_folder='../templates',
        static_folder='../static'
    )

    # Charger la configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    config_class = get_config()
    app.config.from_object(config_class)

    # S'assurer que les dossiers nécessaires existent
    _ensure_directories(app)

    # Initialiser le logging
    setup_logging(app)

    # Initialiser les extensions
    _init_extensions(app)

    # Enregistrer les blueprints
    _register_blueprints(app)

    # Enregistrer les filtres de template
    _register_template_filters(app)

    # Enregistrer les gestionnaires d'erreurs
    _register_error_handlers(app)

    # Enregistrer les SocketIO handlers
    _register_socketio_handlers(app)

    app.logger.info("Application CRM Web V2 créée avec succès")

    return app


def _ensure_directories(app: Flask) -> None:
    """
    S'assure que tous les dossiers nécessaires existent.

    Args:
        app: Instance Flask
    """
    directories = [
        Path(app.config['UPLOAD_PATH']),
        Path(app.config['LOG_PATH']).parent,
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        app.logger.debug(f"Dossier vérifié: {directory}")


def _init_extensions(app: Flask) -> None:
    """
    Initialise les extensions Flask.

    Args:
        app: Instance Flask
    """
    # SocketIO
    socketio.init_app(app, cors_allowed_origins="*")
    app.logger.info("SocketIO initialisé")

    # Rate Limiter
    limiter.init_app(app)
    app.logger.info("Rate Limiter initialisé")


def _register_blueprints(app: Flask) -> None:
    """
    Enregistre tous les blueprints.

    Args:
        app: Instance Flask
    """
    # Blueprint d'authentification
    from app.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)

    # TODO: Ajouter les autres blueprints au fur et à mesure
    # from app.blueprints.dashboard import dashboard_bp
    # from app.blueprints.client import client_bp
    # from app.blueprints.agent import agent_bp
    # from app.blueprints.export import export_bp
    # from app.blueprints.admin import admin_bp
    # from app.blueprints.api import api_bp

    # app.register_blueprint(dashboard_bp)
    # app.register_blueprint(client_bp)
    # app.register_blueprint(agent_bp)
    # app.register_blueprint(export_bp)
    # app.register_blueprint(admin_bp, url_prefix='/admin')
    # app.register_blueprint(api_bp, url_prefix='/api')

    app.logger.info("Blueprints enregistrés")


def _register_template_filters(app: Flask) -> None:
    """
    Enregistre les filtres de template Jinja2.

    Args:
        app: Instance Flask
    """
    from app.utils.helpers import nan_to_empty
    from currency_utils import monnaie

    @app.template_filter('nan_to_empty')
    def nan_to_empty_filter(value):
        """Convertit NaN en chaîne vide."""
        return nan_to_empty(value)

    @app.template_filter('monnaie')
    def monnaie_filter(montant, pays_code='FR'):
        """Formate un montant en devise."""
        return monnaie(montant, pays_code)

    app.logger.debug("Filtres de template enregistrés")


def _register_error_handlers(app: Flask) -> None:
    """
    Enregistre les gestionnaires d'erreurs.

    Args:
        app: Instance Flask
    """
    from flask import render_template, jsonify, request

    @app.errorhandler(404)
    def not_found_error(error):
        """Gestionnaire d'erreur 404."""
        app.logger.warning(f"404 Error: {error}")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Resource not found'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden_error(error):
        """Gestionnaire d'erreur 403."""
        app.logger.warning(f"403 Error: {error}")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Forbidden'}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def internal_error(error):
        """Gestionnaire d'erreur 500."""
        app.logger.error(f"500 Error: {error}")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(429)
    def ratelimit_handler(error):
        """Gestionnaire pour rate limiting."""
        app.logger.warning(f"Rate limit exceeded: {error}")
        return jsonify({
            'error': 'Trop de requêtes. Veuillez réessayer plus tard.',
            'message': str(error)
        }), 429

    app.logger.debug("Gestionnaires d'erreurs enregistrés")


def _register_socketio_handlers(app: Flask) -> None:
    """
    Enregistre les gestionnaires SocketIO.

    Args:
        app: Instance Flask
    """
    from flask_socketio import emit
    from flask import request

    # Stockage en mémoire du chat (à migrer vers DB)
    chat_history = []

    @socketio.on('chat_message')
    def handle_chat_message(data):
        """Gère les messages du chat."""
        try:
            message = {
                'sender': data.get('sender', 'Anonymous'),
                'message': data.get('message', ''),
                'timestamp': data.get('timestamp', '')
            }
            chat_history.append(message)

            # Limiter l'historique à 100 messages
            if len(chat_history) > 100:
                chat_history.pop(0)

            # Diffuser le message à tous les clients
            emit('new_message', message, broadcast=True)
            app.logger.debug(f"Message de chat: {message['sender']}")

        except Exception as e:
            app.logger.error(f"Erreur lors du traitement du message de chat: {e}")

    @socketio.on('chat_history_request')
    def handle_chat_history_request():
        """Envoie l'historique du chat."""
        try:
            emit('chat_history', {'messages': chat_history})
            app.logger.debug("Historique du chat envoyé")
        except Exception as e:
            app.logger.error(f"Erreur lors de l'envoi de l'historique: {e}")

    app.logger.debug("Gestionnaires SocketIO enregistrés")
