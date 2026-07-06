"""
Registre des cibles d'import.

Une « cible » (TargetSpec) décrit comment les features d'un fichier
géospatial deviennent des lignes d'un modèle Django :

  - quel modèle, quel champ géométrique, quelle clé d'upsert ;
  - quelles entrées logiques sont attendues du fichier (ex. "name") —
    le fichier de mapping fait le pont entre ces entrées logiques et les
    vraies colonnes du fichier ;
  - comment normaliser la géométrie (polygone, ligne…) ;
  - comment construire la clé stable et les valeurs du modèle ;
  - un filtre d'acceptation optionnel (ensemble fermé, validation métier).

Phase A : une seule cible, « commune ». Les cibles routes/drainage/terrain
s'ajouteront ici au fil des phases (B, C…), sans toucher au moteur.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from admin_divisions._geo_utils import to_multipolygon
from admin_divisions.communes import canonical_name, commune_code, is_known_commune


@dataclass(frozen=True)
class TargetSpec:
    name: str                       # identifiant de la cible ("commune")
    app_model: str                  # "app_label.ModelName"
    key_field: str                  # champ d'upsert (unique) du modèle
    required_inputs: tuple          # entrées logiques obligatoires ("name", …)
    geometry_field: str             # champ géométrique du modèle
    geometry_label: str             # pour les messages ("polygonale", …)
    normalize_geometry: Callable    # GEOSGeometry -> GEOSGeometry | None
    build_key: Callable             # inputs dict -> {key_field: valeur}
    build_defaults: Callable        # inputs dict -> defaults du modèle
    accept: Optional[Callable] = None  # inputs dict -> (bool, raison)

    def model(self):
        from django.apps import apps
        return apps.get_model(self.app_model)


_TARGETS = {}


def register(spec: TargetSpec):
    _TARGETS[spec.name] = spec
    return spec


def get_target(name):
    if name not in _TARGETS:
        available = ", ".join(sorted(_TARGETS)) or "(aucune)"
        raise KeyError(
            f"Cible d'import inconnue : « {name} ». Disponibles : {available}."
        )
    return _TARGETS[name]


def available_targets():
    return sorted(_TARGETS)


# ── Cible « commune » ───────────────────────────────────────────────────────

def _commune_accept(inputs):
    if not is_known_commune(inputs["name"]):
        return False, f"commune non reconnue : « {inputs['name']} »"
    return True, ""


register(TargetSpec(
    name="commune",
    app_model="admin_divisions.Commune",
    key_field="code",
    required_inputs=("name",),
    geometry_field="geom",
    geometry_label="polygonale",
    normalize_geometry=to_multipolygon,
    build_key=lambda inputs: {"code": commune_code(inputs["name"])},
    build_defaults=lambda inputs: {
        "name": canonical_name(inputs["name"]),
        # Pas de hiérarchie pour l'instant : converge vers NULL (cf. décision
        # « seules les communes du GeoJSON », raccrochage BDR plus tard).
        "district": None,
        "ville": None,
        "region": None,
        "departement": None,
        "sous_prefecture": None,
    },
    accept=_commune_accept,
))
