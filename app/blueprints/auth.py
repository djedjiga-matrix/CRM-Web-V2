"""
Blueprint d'authentification pour CRM Web V2.

Ce module gère la connexion, la déconnexion et la vérification de session.
"""
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, jsonify
from app.models.agent import AgentDAO
from app.models.journal import JournalDAO
from app.extensions import limiter
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Créer le blueprint
auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute;20 per hour")
def login():
    """
    Route de connexion.

    GET: Affiche le formulaire de connexion
    POST: Authentifie l'utilisateur
    """
    # Si déjà connecté, rediriger vers le dashboard
    if 'agent_nom' in session:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        login_input = request.form.get('login', '').strip()
        password = request.form.get('password', '')

        if not login_input or not password:
            flash('Veuillez remplir tous les champs', 'warning')
            return render_template('login.html')

        try:
            # Initialiser les DAO
            from flask import current_app
            agent_dao = AgentDAO(current_app.config['DB_PATH'])
            journal_dao = JournalDAO(current_app.config['DB_PATH'])

            # Authentifier l'agent
            agent = agent_dao.authenticate(login_input, password)

            if agent:
                # Créer la session
                session.clear()
                session['agent_nom'] = agent['NOM']
                session['agent_login'] = agent['LOGIN']
                session['agent_role'] = agent.get('ROLE', 'agent')
                session['agent_id'] = agent['id']
                session['campagne_id'] = agent.get('campagne_id')
                session['pays_code'] = agent.get('PAYS_CODE', 'FR')
                session.permanent = True

                # Enregistrer dans le journal
                journal_dao.log_connexion(
                    agent_nom=agent['NOM'],
                    page='login',
                    type_event='connexion'
                )

                flash(f'Bienvenue {agent["NOM"]} !', 'success')
                logger.info(f"Connexion réussie: {agent['LOGIN']} ({agent['NOM']})")

                # Rediriger selon la campagne
                campagne_id = agent.get('campagne_id')
                if campagne_id == 2:  # VALANDRE
                    return redirect(url_for('dashboard.valandre'))
                elif campagne_id == 3:  # HUMANITAIRE
                    return redirect(url_for('dashboard.humanitaire'))
                else:  # SFR ou défaut
                    return redirect(url_for('dashboard.index'))
            else:
                flash('Login ou mot de passe incorrect', 'danger')
                logger.warning(f"Échec de connexion pour: {login_input}")

        except Exception as e:
            logger.error(f"Erreur lors de l'authentification: {e}")
            flash('Erreur lors de la connexion', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Route de déconnexion."""
    agent_nom = session.get('agent_nom', 'Inconnu')

    try:
        # Enregistrer la déconnexion
        from flask import current_app
        journal_dao = JournalDAO(current_app.config['DB_PATH'])
        journal_dao.log_connexion(
            agent_nom=agent_nom,
            page='logout',
            type_event='déconnexion'
        )
    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement de la déconnexion: {e}")

    # Nettoyer la session
    session.clear()

    flash('Vous êtes déconnecté', 'info')
    logger.info(f"Déconnexion: {agent_nom}")

    return redirect(url_for('auth.login'))


@auth_bp.route('/is_logged_in')
def is_logged_in():
    """
    Vérifie si l'utilisateur est connecté (endpoint API).

    Returns:
        JSON avec le statut de connexion
    """
    if 'agent_nom' in session:
        return jsonify({
            'logged_in': True,
            'agent': session['agent_nom'],
            'role': session.get('agent_role', 'agent')
        })
    else:
        return jsonify({
            'logged_in': False
        }), 401
