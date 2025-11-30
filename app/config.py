"""
Configuration centralisée pour CRM Web V2.

Ce module fournit une configuration centralisée et sécurisée pour l'application.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement
basedir = Path(__file__).parent.parent
load_dotenv(basedir / '.env')


class Config:
    """Configuration de base."""

    # Sécurité
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'

    # Base de données
    DB_NAME = os.environ.get('DB_NAME', 'crm_clients.db')
    DB_PATH = str(basedir / DB_NAME)
    HUMA_DB_NAME = os.environ.get('HUMA_DB_NAME', 'humanitaire.db')
    HUMA_DB_PATH = str(basedir / HUMA_DB_NAME)

    # Upload
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
    UPLOAD_PATH = str(basedir / UPLOAD_FOLDER)
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))  # 16MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

    # Aircall API
    AIRCALL_API_ID = os.environ.get('AIRCALL_API_ID')
    AIRCALL_API_TOKEN = os.environ.get('AIRCALL_API_TOKEN')

    # Data paths
    HUMA_APPELS_GLOB = os.environ.get('HUMA_APPELS_GLOB', 'Data/*.xlsx')
    HUMA_GRH_GLOB = os.environ.get('HUMA_GRH_GLOB', 'Data/*.xlsx')

    # Rate limiting
    RATELIMIT_STORAGE_URL = os.environ.get('RATELIMIT_STORAGE_URL', 'memory://')
    RATELIMIT_DEFAULT = "200 per day;50 per hour"

    # Session
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 heure

    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.environ.get('LOG_FILE', 'logs/crm.log')
    LOG_PATH = str(basedir / LOG_FILE)

    # Business constants
    TAUX_EUR_VERS_DT = 3.30  # Taux de change EUR -> TND

    # Campagnes
    CAMPAGNE_SFR = 1
    CAMPAGNE_VALANDRE = 2
    CAMPAGNE_HUMANITAIRE = 3

    # Statuts
    STATUTS_VALIDES = ['valide', 'validé', 'validée']
    STATUTS_NON_VALIDES = ['non valide', 'non validé', 'refusé']


class DevelopmentConfig(Config):
    """Configuration de développement."""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Configuration de production."""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True  # HTTPS requis


class TestingConfig(Config):
    """Configuration de test."""
    TESTING = True
    DEBUG = True
    DB_NAME = 'test_crm_clients.db'
    HUMA_DB_NAME = 'test_humanitaire.db'


# Dictionnaire de configurations
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Retourne la configuration appropriée basée sur FLASK_ENV."""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
