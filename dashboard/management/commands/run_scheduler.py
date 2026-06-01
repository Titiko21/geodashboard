"""
run_scheduler.py — GéoDash
Scheduler interne basé sur APScheduler. Lance les commandes Django
récurrentes (rafraîchissement OSM, scoring GEE) sans dépendre d'un cron
système ni d'un broker externe.

Conçu pour tourner dans un service Docker dédié (image identique à `web`).

Jobs par défaut (Africa/Abidjan) :
  - populate_geodata  → dimanche 03:00 (refresh OSM hebdomadaire)
  - update_gee_scores → tous les jours 04:00

Variables d'environnement (toutes optionnelles) :
  SCHEDULER_DISABLED       = "1" pour désactiver entièrement
  SCHED_OSM_DOW            = jour OSM (défaut "sun")
  SCHED_OSM_HOUR           = heure OSM (défaut 3)
  SCHED_OSM_MINUTE         = minute OSM (défaut 0)
  SCHED_GEE_HOUR           = heure GEE (défaut 4)
  SCHED_GEE_MINUTE         = minute GEE (défaut 0)
  SCHED_RUN_ON_START       = "1" pour lancer un cycle immédiat (debug)
"""
import logging
import os
import signal
import sys

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger("dashboard")

TZ = "Africa/Abidjan"


def _env(name, default):
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _job_populate_geodata():
    logger.info("[scheduler] Démarrage populate_geodata...")
    try:
        call_command("populate_geodata")
        logger.info("[scheduler] populate_geodata terminé.")
    except Exception:
        logger.exception("[scheduler] populate_geodata a échoué.")


def _job_update_gee_scores():
    logger.info("[scheduler] Démarrage update_gee_scores...")
    try:
        call_command("update_gee_scores")
        logger.info("[scheduler] update_gee_scores terminé.")
    except Exception:
        logger.exception("[scheduler] update_gee_scores a échoué.")


class Command(BaseCommand):
    help = "Lance le scheduler APScheduler pour les jobs récurrents (OSM, GEE)."

    def handle(self, *args, **options):
        if _env("SCHEDULER_DISABLED", "0") == "1":
            self.stdout.write(self.style.WARNING(
                "SCHEDULER_DISABLED=1 — scheduler désactivé, sortie."
            ))
            return

        osm_dow    = _env("SCHED_OSM_DOW", "sun")
        osm_hour   = int(_env("SCHED_OSM_HOUR", 3))
        osm_minute = int(_env("SCHED_OSM_MINUTE", 0))
        gee_hour   = int(_env("SCHED_GEE_HOUR", 4))
        gee_minute = int(_env("SCHED_GEE_MINUTE", 0))

        scheduler = BlockingScheduler(
            timezone=TZ,
            executors={"default": ThreadPoolExecutor(max_workers=2)},
            job_defaults={
                "coalesce":      True,
                "max_instances": 1,
                "misfire_grace_time": 60 * 60,
            },
        )

        scheduler.add_job(
            _job_populate_geodata,
            CronTrigger(day_of_week=osm_dow, hour=osm_hour, minute=osm_minute, timezone=TZ),
            id="populate_geodata",
            name="OSM Overpass refresh (hebdomadaire)",
            replace_existing=True,
        )

        scheduler.add_job(
            _job_update_gee_scores,
            CronTrigger(hour=gee_hour, minute=gee_minute, timezone=TZ),
            id="update_gee_scores",
            name="GEE scores refresh (quotidien)",
            replace_existing=True,
        )

        self.stdout.write(self.style.SUCCESS(
            f"[scheduler] OSM: {osm_dow} {osm_hour:02d}:{osm_minute:02d} {TZ}  |  "
            f"GEE: tous les jours {gee_hour:02d}:{gee_minute:02d} {TZ}"
        ))
        logger.info(
            "[scheduler] Démarrage — OSM=%s %02d:%02d, GEE=%02d:%02d (%s)",
            osm_dow, osm_hour, osm_minute, gee_hour, gee_minute, TZ,
        )

        if _env("SCHED_RUN_ON_START", "0") == "1":
            self.stdout.write(self.style.WARNING(
                "[scheduler] SCHED_RUN_ON_START=1 — lancement immédiat des deux jobs."
            ))
            scheduler.add_job(_job_populate_geodata, id="boot_osm")
            scheduler.add_job(_job_update_gee_scores, id="boot_gee")

        def _shutdown(signum, frame):
            self.stdout.write(self.style.WARNING(
                f"\n[scheduler] Signal {signum} reçu — arrêt propre..."
            ))
            scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown(wait=False)
