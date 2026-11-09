"""Provider modules and registry for fetching wallpaper images from external APIs."""

import os
import importlib
import pkgutil
from typing import Dict

from models import SourceSpec

SOURCES: Dict[str, SourceSpec] = {}

# Définit le chemin absolu vers le dossier Sources/
sources_dir = os.path.join(os.path.dirname(__file__), "Sources")

# Parcours dynamique de tous les modules .py présents dans le dossier
for _, module_name, _ in pkgutil.iter_modules([sources_dir]):
    try:
        # Importation du module (ex: Sources.nasa, Sources.wallhaven)
        module = importlib.import_module(f"Sources.{module_name}")

        # Si le module expose une variable SPEC valide, on l'ajoute au registre
        if hasattr(module, "SPEC") and isinstance(module.SPEC, SourceSpec):
            # La clé du dictionnaire sera le nom du fichier (sans le .py)
            SOURCES[module_name] = module.SPEC

    except Exception as e:
        print(f"Erreur lors du chargement de la source '{module_name}': {e}")
