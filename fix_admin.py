# fix_admin.py — crée ou met à jour un compte admin avec mot de passe bcrypt
import os, sqlite3, argparse, sys
import bcrypt

DB_NAME = os.getenv("DB_NAME", "crm_clients.db")  # même défaut que l'app

def ensure_agents_table(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            NOM TEXT NOT NULL UNIQUE,
            LOGIN TEXT NOT NULL UNIQUE,
            MDP TEXT NOT NULL,
            ROLE TEXT NOT NULL DEFAULT 'agent',
            campagne_id INTEGER DEFAULT 1,
            PAYS_CODE TEXT,
            photo TEXT,
            TV TEXT
        )
    """)
    conn.commit()

def upsert_admin(login, password, nom, role, campagne_id=1):
    # hash bcrypt (UTF-8 -> bytes)
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    conn = sqlite3.connect(DB_NAME)
    try:
        ensure_agents_table(conn)
        c = conn.cursor()
        # existe ?
        c.execute("SELECT id FROM agents WHERE LOGIN = ?", (login,))
        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE agents
                   SET NOM = ?, MDP = ?, ROLE = ?, campagne_id = ?
                 WHERE id = ?
            """, (nom, pw_hash.decode("utf-8"), role, campagne_id, row[0]))
            print(f"✅ Admin mis à jour: {login}")
        else:
            c.execute("""
                INSERT INTO agents (NOM, LOGIN, MDP, ROLE, campagne_id)
                VALUES (?, ?, ?, ?, ?)
            """, (nom, login, pw_hash.decode("utf-8"), role, campagne_id))
            print(f"✅ Admin créé: {login}")
        conn.commit()
    finally:
        conn.close()

def main():
    ap = argparse.ArgumentParser(description="Créer/MàJ un admin avec bcrypt")
    ap.add_argument("--login", required=True, help="ex: admin@moncrm.com")
    ap.add_argument("--password", required=True, help="mot de passe en clair")
    ap.add_argument("--name", default="admin", help="Nom affiché")
    ap.add_argument("--role", default="admin", help="admin/agent/... (def=admin)")
    ap.add_argument("--campagne_id", type=int, default=1)
    args = ap.parse_args()
    upsert_admin(args.login, args.password, args.name, args.role, args.campagne_id)

if __name__ == "__main__":
    # Vérifie bcrypt installé
    try:
        main()
    except ModuleNotFoundError:
        print("❌ Module 'bcrypt' manquant. Installe-le avec :")
        print("   pip install bcrypt")
        sys.exit(1)
