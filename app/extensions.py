"""
Extensions Flask pour CRM Web V2.

Ce module initialise toutes les extensions Flask utilisées par l'application.
"""
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# SocketIO pour le chat en temps réel
socketio = SocketIO()

# Rate limiting pour protéger contre les abus
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)
