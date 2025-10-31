# check_imports.py
# Analyse les imports du projet, signale ceux qui échouent et liste les fichiers .py orphelins.
from __future__ import annotations
import argparse, ast, sys, os, importlib, traceback
from pathlib import Path
from collections import defaultdict

STD_EXCLUDES = {
    # dossiers à ignorer
    ".venv", "venv", "__pycache__", ".git",
}

FILE_EXCLUDES = {
    # fichiers à ignorer (ajoute si besoin)
    "check_imports.py",
}

def is_ignored(path: Path) -> bool:
    # on rassemble les noms des dossiers parents + le fichier
    parts = set([p.name for p in path.parents])
    parts.add(path.name)
    if any(part in STD_EXCLUDES for part in parts):
        return True
    if path.name in FILE_EXCLUDES:
        return True
    return False

def iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if not is_ignored(p):
            yield p

def parse_imports(py_file: Path):
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except Exception as e:
        return [], [f"!! PARSE ERROR {py_file}: {e}"]
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append((n.name, None, py_file))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for n in node.names:
                imports.append((mod, n.name, py_file))
    return imports, []

def try_import(module_name: str, project_root: Path):
    """
    Essaie d'importer un module 'module_name' en ajoutant le project_root à sys.path.
    Retourne (ok: bool, error: str|None, file: str|None)
    """
    # Important: garantir root en tête
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        m = importlib.import_module(module_name)
        return True, None, getattr(m, "__file__", None)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None

def main():
    ap = argparse.ArgumentParser(description="Diagnostique les imports du projet et signale les fichiers orphelins.")
    ap.add_argument("--root", default=".", help="Racine du projet (par défaut: .)")
    ap.add_argument("--show-orphans", action="store_true", help="Affiche les .py non importés par d'autres.")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    print(f"[INFO] Racine du projet: {root}")

    all_files = list(iter_py_files(root))
    print(f"[INFO] Fichiers .py scannés: {len(all_files)}")

    # Collecte des imports
    imported_modules = defaultdict(set)  # module -> {fichiers qui importent}
    import_errors = defaultdict(list)    # module -> [erreurs]
    parse_errors = []
    file_to_imports = defaultdict(set)   # fichier -> {modules importés}

    for py in all_files:
        imports, errs = parse_imports(py)
        if errs:
            parse_errors.extend(errs)
        for mod, name, origin in imports:
            if not mod:
                continue
            # On prend seulement la partie top-level du module (ex: "package.sub.mod" -> "package")
            top = mod.split(".")[0]
            imported_modules[top].add(str(origin))
            file_to_imports[str(origin)].add(top)

    # Essaie d'importer chaque module top-level importé
    # On ne signale que ceux qui ressemblent à des modules locaux du projet ou à installer
    for module in sorted(imported_modules.keys()):
        ok, err, where = try_import(module, root)
        if not ok:
            import_errors[module].append(err)

    # Orphelins: fichiers jamais importés par d'autres (exclu ceux qui sont des "entrypoints" classiques)
    entrypoint_candidates = {"app.py", "etl_import.py", "etl_incremental.py",
                             "import_huma_to_db.py", "update_clients_table.py",
                             "add_adresse2.py", "add_role_column.py", "set_admin.py", "fix_admin.py"}
    reverse_index = defaultdict(set)  # fichier -> set(fichier qui l'importe)
    # Pour un vrai graphe d'import complet, il faudrait résoudre les chemins; ici, simple heuristique:
    for importer, mods in file_to_imports.items():
        for m in mods:
            # tente de retrouver un .py local qui correspond au module
            candidate = root / f"{m}.py"
            if candidate.exists():
                reverse_index[str(candidate.resolve())].add(importer)

    orphans = []
    for f in all_files:
        if f.name in entrypoint_candidates:
            continue
        if "tests" in f.parts:
            continue
        if str(f.resolve()) not in reverse_index:
            orphans.append(f)

    # ----- Rapport -----
    print("\n===== RAPPORT IMPORTS =====")

    if parse_errors:
        print("\n[!] Erreurs de parsing Python (à corriger) :")
        for e in parse_errors:
            print("   -", e)

    if import_errors:
        print("\n[!] Imports qui échouent (module introuvable ou lib manquante) :")
        for mod, errs in import_errors.items():
            print(f"   - {mod}")
            for e in errs:
                print(f"       -> {e}")
        print("\nAstuce:")
        print(" - Si c'est un module DE TON PROJET : vérifie qu'il y a bien un fichier .py à la racine,")
        print("   ou que le dossier est un package (fichier __init__.py), et importe avec un chemin absolu depuis la racine du projet.")
        print(" - Si c'est une LIB Python manquante : installe-la dans le venv, ex:  pip install nom_de_lib")
    else:
        print("\n✅ Aucun import cassé détecté (au niveau top-level).")

    if args.show_orphans:
        print("\n===== FICHIERS ORPHELINS (non importés par d'autres) =====")
        if orphans:
            for f in sorted(orphans):
                print("   -", f.relative_to(root))
            print("\nNote: Ce n'est pas forcément un problème (scripts exécutables, CLI, etc.),")
            print("mais s'ils devaient être des modules réutilisables, pense à les importer quelque part")
            print("ou à clarifier leur rôle (ex: les déplacer dans un dossier 'scripts/').")
        else:
            print("✅ Aucun fichier orphelin significatif.")
    print("\n[FIN]")

if __name__ == "__main__":
    main()
