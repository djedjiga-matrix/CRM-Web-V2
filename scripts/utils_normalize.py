# utils_normalize.py
# Module centralisé de nettoyage et de détection d'état appel

import unicodedata
import re

def strip_accents(s: str) -> str:
    """Supprime les accents et met en minuscule."""
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def normalize_text(s: str) -> str:
    """Normalise le texte pour comparaison (accents, casse, espaces)."""
    return strip_accents(s or "").lower().strip()

# ----------------------------------------------------------
# Détection robuste des statuts (don, refus, etc.)
# ----------------------------------------------------------

KEYWORDS = {
    "don": [r"\bdon\b", r"ok", r"accepte", r"oui", r"valide"],
    "don_mail": [r"mail", r"email", r"en ligne", r"web"],
    "indecis": [r"indecis", r"reflech", r"rappel", r"voir"],
    "refus": [r"refus", r"non", r"pas interesse", r"raccroche", r"ne veux pas"],
}

def detect_status(text: str) -> str:
    """Retourne le statut standardisé selon le texte fourni."""
    txt = normalize_text(text)
    if not txt:
        return "inconnu"
    for label, patterns in KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, txt):
                return label
    return "autre"
