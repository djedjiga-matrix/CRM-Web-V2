"""
Système de logging centralisé pour CRM Web V2.

Ce module fournit une configuration de logging structurée avec rotation
et niveaux de log appropriés.
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logging(app) -> logging.Logger:
    """
    Configure le système de logging pour l'application.

    Args:
        app: Instance Flask

    Returns:
        Logger configuré
    """
    # Créer le dossier logs s'il n'existe pas
    log_dir = Path(app.config['LOG_PATH']).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Niveau de log
    log_level = getattr(logging, app.config['LOG_LEVEL'].upper(), logging.INFO)

    # Format des logs
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s (%(funcName)s:%(lineno)d): %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler pour fichier avec rotation
    file_handler = RotatingFileHandler(
        app.config['LOG_PATH'],
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    # Handler pour console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # Configurer le logger de l'application
    app.logger.setLevel(log_level)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)

    # Désactiver les logs de Werkzeug en production
    if not app.debug:
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

    app.logger.info('='*60)
    app.logger.info('Application CRM Web V2 démarrée')
    app.logger.info(f'Environment: {app.config.get("FLASK_ENV", "development")}')
    app.logger.info(f'Debug mode: {app.debug}')
    app.logger.info('='*60)

    return app.logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Retourne un logger pour un module spécifique.

    Args:
        name: Nom du module (généralement __name__)

    Returns:
        Logger configuré
    """
    return logging.getLogger(name or __name__)
