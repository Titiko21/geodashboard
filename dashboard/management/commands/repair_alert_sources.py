"""
Répare les alertes existantes en leur attribuant une référence directe vers
leur objet source (source_type / source_id), puis recalcule leurs coordonnées
sur le centroïde réel de cet objet.

Contexte
--------
Historiquement, le repositionnement des alertes résolvait l'objet source par
son NOM. Or plusieurs segments/zones peuvent porter le même nom : la résolution
tombait alors sur le mauvais objet et l'alerte « renvoyait ailleurs ».

Cette commande effectue le backfill, une fois pour toutes, du lien direct :

    Alert.source_type + Alert.source_id  ->  objet exact

Stratégie de résolution (par alerte non encore liée)
----------------------------------------------------
1. Candidats = objets de la zone portant le nom extrait du titre.
2. Si un seul candidat -> retenu.
3. Si plusieurs -> on désambiguïse par le score embarqué dans le message
   (condition_score pour les routes, risk_score pour les inondations).
4. Si toujours ambigu -> on retient le candidat dont le centroïde est le plus
   proche des coordonnées actuelles de l'alerte.

Idempotente : relançable sans effet de bord. Les alertes déjà liées voient
seulement leurs coordonnées re-synchronisées.

Exemples
--------
    python manage.py repair_alert_sources --dry-run
    python manage.py repair_alert_sources
    python manage.py repair_alert_sources --zone ABJ
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from dashboard.models import (
    Alert,
    FloodRisk,
    RoadSegment,
    VegetationDensity,
    Zone,
)
from dashboard.management.commands.populate_geodata import _geojson_centroid


SCORE_RE = re.compile(r"score\s+(\d+)\s*/\s*100")

# Catégorie -> (préfixe de titre, modèle, champ score | None)
RESOLVERS = {
    "road":       ("Route dégradée : ",      RoadSegment,       "condition_score"),
    "flood":      ("Risque inondation : ",   FloodRisk,         "risk_score"),
    "vegetation": ("Végétation dégradée : ", VegetationDensity, None),
}


def _name_from_title(title, prefix):
    """Extrait le nom de l'objet à partir du titre de l'alerte."""
    if title.startswith(prefix):
        return title[len(prefix):].strip()
    return title.strip()


def _score_from_message(message):
    """Score (int) embarqué dans le message, ou None."""
    m = SCORE_RE.search(message or "")
    return int(m.group(1)) if m else None


def _nearest(candidates, lat, lng):
    """Candidat dont le centroïde est le plus proche de (lat, lng)."""
    if lat is None or lng is None:
        return candidates[0]
    best, best_d = None, None
    for obj in candidates:
        clat, clng = _geojson_centroid(getattr(obj, "geojson", None))
        if clat is None:
            continue
        d = (clat - lat) ** 2 + (clng - lng) ** 2
        if best_d is None or d < best_d:
            best, best_d = obj, d
    return best or candidates[0]


def _resolve_source(alert, candidates_by_name):
    """
    Retourne l'objet source de l'alerte, ou None si non résolvable.

    `candidates_by_name` : dict { name -> [objets] } pré-construit pour la zone
    et la catégorie de l'alerte.
    """
    cfg = RESOLVERS.get(alert.category)
    if cfg is None:
        return None
    prefix, _model, score_field = cfg

    name = _name_from_title(alert.title, prefix)
    candidates = candidates_by_name.get(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Désambiguïsation par score embarqué dans le message
    if score_field:
        score = _score_from_message(alert.message)
        if score is not None:
            scored = [
                o for o in candidates
                if getattr(o, score_field, None) == score
            ]
            if len(scored) == 1:
                return scored[0]
            if len(scored) > 1:
                candidates = scored

    # Dernier recours : le plus proche des coordonnées actuelles
    return _nearest(candidates, alert.lat, alert.lng)


class Command(BaseCommand):
    help = (
        "Backfill du lien Alert->objet source (source_type/source_id) et "
        "repositionnement des alertes sur la géométrie réelle de leur source."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--zone", type=str, default=None,
            help="Code zone (ex: ABJ). Absent = toutes les zones.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Simule sans écrire en base.",
        )

    def handle(self, *args, **options):
        dry_run   = options["dry_run"]
        zone_code = options["zone"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN -- aucune écriture en base\n"))

        zones = Zone.objects.all()
        if zone_code:
            zones = zones.filter(code__iexact=zone_code)
            if not zones.exists():
                available = ", ".join(Zone.objects.values_list("code", flat=True))
                self.stderr.write(
                    f"Zone '{zone_code}' introuvable. Codes : {available}"
                )
                return

        totals = {
            "seen": 0, "linked": 0, "relinked": 0,
            "coords": 0, "unresolved": 0,
        }
        unresolved_samples = []

        for zone in zones:
            alerts = list(Alert.objects.filter(zone=zone))
            if not alerts:
                continue

            cand_cache = {}

            def candidates_for(category):
                if category in cand_cache:
                    return cand_cache[category]
                cfg = RESOLVERS.get(category)
                if cfg is None:
                    cand_cache[category] = {}
                    return cand_cache[category]
                _prefix, model, score_field = cfg
                fields = ["id", "name", "geojson"]
                if score_field:
                    fields.append(score_field)
                by_name = {}
                for obj in model.objects.filter(zone=zone).only(*fields):
                    by_name.setdefault(obj.name, []).append(obj)
                cand_cache[category] = by_name
                return by_name

            to_update = []

            for alert in alerts:
                totals["seen"] += 1

                # Résolution de l'objet source
                if alert.source_id is not None and alert.source_type:
                    obj = self._fetch(alert.source_type, alert.source_id)
                    newly_linked = False
                else:
                    obj = _resolve_source(alert, candidates_for(alert.category))
                    newly_linked = obj is not None

                if obj is None:
                    totals["unresolved"] += 1
                    if len(unresolved_samples) < 10:
                        unresolved_samples.append(f"[{zone.code}] {alert.title}")
                    continue

                changed = []

                if newly_linked:
                    alert.source_type = alert.category
                    alert.source_id  = obj.id
                    changed += ["source_type", "source_id"]
                    totals["linked"] += 1
                elif (alert.source_type != alert.category
                      or alert.source_id != obj.id):
                    alert.source_type = alert.category
                    alert.source_id  = obj.id
                    changed += ["source_type", "source_id"]
                    totals["relinked"] += 1

                # Repositionnement sur le centroïde réel
                lat, lng = _geojson_centroid(getattr(obj, "geojson", None))
                if lat is not None and (alert.lat != lat or alert.lng != lng):
                    alert.lat, alert.lng = lat, lng
                    changed += ["lat", "lng"]
                    totals["coords"] += 1

                if changed:
                    to_update.append((alert, changed))

            if not dry_run and to_update:
                with transaction.atomic():
                    for alert, fields in to_update:
                        alert.save(update_fields=list(set(fields)))

            self.stdout.write(
                f"  {zone.code:<6} {len(alerts):>4} alertes — "
                f"liées {sum(1 for a, f in to_update if 'source_id' in f)}, "
                f"coords {sum(1 for a, f in to_update if 'lat' in f)}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Total : {totals['seen']} alertes vues — "
            f"{totals['linked']} liées, {totals['relinked']} re-liées, "
            f"{totals['coords']} repositionnées, "
            f"{totals['unresolved']} non résolues."
        ))
        if unresolved_samples:
            self.stdout.write(self.style.WARNING(
                "Exemples non résolus (objet source absent en base) :"
            ))
            for s in unresolved_samples:
                self.stdout.write(f"    - {s}")

    @staticmethod
    def _fetch(source_type, source_id):
        cfg = RESOLVERS.get(source_type)
        if cfg is None:
            return None
        model = cfg[1]
        return model.objects.filter(id=source_id).only("id", "geojson").first()
