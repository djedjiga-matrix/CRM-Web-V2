#!/usr/bin/env python3
"""
Point d'entrée pour CRM Web V2.

Lance l'application Flask avec SocketIO.
"""
import os
from app import create_app
from app.extensions import socketio


# Créer l'application
app = create_app()


if __name__ == '__main__':
    # Configuration pour le développement
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = app.config.get('DEBUG', False)

    # IMPORTANT: Ne jamais activer debug=True en production
    if debug:
        app.logger.warning("="*60)
        app.logger.warning("MODE DEBUG ACTIVÉ - NE PAS UTILISER EN PRODUCTION")
        app.logger.warning("="*60)

    # Lancer l'application avec SocketIO
    socketio.run(
        app,
        host=host,
        port=port,
        debug=False,  # TOUJOURS False en production pour la sécurité
        use_reloader=debug,
        log_output=True
    )
