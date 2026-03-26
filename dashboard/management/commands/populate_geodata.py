"""
populate_geodata.py — GéoDash
Importe les données géospatiales depuis OpenStreetMap via l'API Overpass.

Optimisation principale : une seule requête Overpass par zone qui récupère
routes + eau + végétation en même temps.

Améliorations v2 :
  - Clé unique par osm_id (plus de collisions sur les noms)
  - Suppression automatique des éléments disparus d'OSM
  - Gestion d'erreur par élément (un échec ne plante pas tout)
  - Factorisation DRY des 3 fonctions save_*
  - Fermeture automatique des polygones
  - Backoff adaptatif + rotation d'instances Overpass

PRÉREQUIS : ajouter osm_id aux modèles avant d'utiliser ce fichier.
  Voir models_patch.py (livré avec ce fichier) et lancer :
    python manage.py makemigrations
    python manage.py migrate
    python manage.py populate_geodata --clear   # réimport complet

Usage :
    python manage.py populate_geodata                  # toutes les zones CI
    python manage.py populate_geodata --zone MAN       # une seule ville
    python manage.py populate_geodata --dry-run        # voir sans écrire
    python manage.py populate_geodata --clear          # repart de zéro
    python manage.py populate_geodata --roads-only     # routes uniquement
"""

import logging
import math
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dashboard.models import Alert, FloodRisk, RoadSegment, VegetationDensity, Zone

logger = logging.getLogger(__name__)


# ─── Config Overpass ──────────────────────────────────────────────────────────

OVERPASS_INSTANCES = [
    os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
_overpass_instance_index = 0

OVERPASS_TIMEOUT = 90
REQUEST_DELAY    = 12.0
MAX_RETRIES      = 3
RETRY_DELAY      = 15.0
SEARCH_RADIUS    = 0.05
MAX_ELEMENTS     = 500

_consecutive_429 = 0
_MAX_GLOBAL_WAIT = 300


# ─── Villes de Côte d'Ivoire ──────────────────────────────────────────────────

COTE_IVOIRE_VILLES = [

    # ── District Autonome d'Abidjan ───────────────────────────────────────────
    ("Abidjan",           "ABJ",   5.3600,  -4.0083, "Capitale économique, plus grande ville du pays"),
    ("Abobo",             "ABO",   5.4167,  -4.0167, "Commune nord d'Abidjan"),
    ("Adjamé",            "ADJ",   5.3667,  -4.0167, "Commune centrale d'Abidjan"),
    ("Cocody",            "COC",   5.3667,  -3.9667, "Commune résidentielle d'Abidjan"),
    ("Yopougon",          "YOP",   5.3500,  -4.0833, "Plus grande commune d'Abidjan"),
    ("Marcory",           "MAR",   5.3000,  -3.9833, "Commune sud d'Abidjan"),
    ("Koumassi",          "KOU",   5.2833,  -3.9667, "Commune industrielle d'Abidjan"),
    ("Port-Bouët",        "PBO",   5.2500,  -3.9333, "Commune aéroportuaire d'Abidjan"),
    ("Treichville",       "TRE",   5.2833,  -4.0000, "Commune portuaire d'Abidjan"),
    ("Plateau",           "PLT",   5.3167,  -4.0167, "Centre des affaires d'Abidjan"),
    ("Attécoubé",         "ATT",   5.3500,  -4.0500, "Commune ouest d'Abidjan"),
    ("Bingerville",       "BNG",   5.3500,  -3.8833, "Ancienne capitale coloniale"),
    ("Anyama",            "ANY",   5.5000,  -4.0500, "Banlieue nord d'Abidjan"),
    ("Songon",            "SON",   5.3167,  -4.2833, "Commune rurale d'Abidjan"),

    # ── District Autonome de Yamoussoukro ─────────────────────────────────────
    ("Yamoussoukro",      "YAM",   6.8276,  -5.2893, "Capitale politique, basilique Notre-Dame de la Paix"),
    ("Attiégouakro",      "ATG",   6.9333,  -5.4333, "Sous-préfecture de Yamoussoukro"),

    # ── Région des Grands-Ponts ───────────────────────────────────────────────
    ("Dabou",             "DAB",   5.3167,  -4.3833, "Chef-lieu des Grands-Ponts"),
    ("Grand-Lahou",       "GLA",   5.1333,  -5.0167, "Lagune Tagba"),
    ("Jacqueville",       "JAC",   5.2000,  -4.4167, "Péninsule des Grands-Ponts"),
    ("Sikensi",           "SKS",   5.6500,  -4.5833, "Région des Grands-Ponts"),
    ("Toupah",            "TPA",   5.3833,  -4.7500, "Sous-préfecture des Grands-Ponts"),
    ("Toupa",             "TPP",   5.2833,  -4.6167, "Sous-préfecture des Grands-Ponts"),

    # ── Région de l'Agnéby-Tiassa ─────────────────────────────────────────────
    ("Agboville",         "AGB",   5.9333,  -4.2167, "Chef-lieu de l'Agnéby-Tiassa"),
    ("Tiassalé",          "TIA",   5.8833,  -4.8167, "Sur le fleuve Bandama"),
    ("Taabo",             "TAO",   6.2167,  -5.1167, "Barrage hydroélectrique sur le Bandama"),
    ("Azaguié",           "AZG",   5.6333,  -4.0833, "Sous-préfecture de l'Agnéby-Tiassa"),
    ("N'Douci",           "NDC",   5.8167,  -4.6833, "Sous-préfecture de l'Agnéby-Tiassa"),
    ("Céchi",             "CEC",   5.6833,  -3.9167, "Sous-préfecture de l'Agnéby-Tiassa"),

    # ── Région de la Mé ───────────────────────────────────────────────────────
    ("Adzopé",            "ADZ",   6.1000,  -3.8667, "Chef-lieu de la Mé"),
    ("Alépé",             "ALE",   5.5000,  -3.6667, "Région de la Mé, bord de lagune"),
    ("Akoupé",            "AKP",   6.3833,  -3.8667, "Région de la Mé"),
    ("Yakassé-Attobrou",  "YKA",   6.2000,  -3.7333, "Sous-préfecture de la Mé"),

    # ── Région du Sud-Comoé ───────────────────────────────────────────────────
    ("Grand-Bassam",      "GBA",   5.2000,  -3.7333, "Patrimoine UNESCO, ancienne capitale coloniale"),
    ("Aboisso",           "ABS",   5.4667,  -3.2000, "Chef-lieu du Sud-Comoé"),
    ("Adiaké",            "ADA",   5.2833,  -3.3000, "Lagune Tendo, frontière Ghana"),
    ("Bonoua",            "BON",   5.2667,  -3.5833, "Sous-préfecture du Sud-Comoé"),
    ("Tiapoum",           "TPM",   5.1667,  -3.0500, "Frontière avec le Ghana"),
    ("Ayamé",             "AYM",   5.6167,  -3.1667, "Barrage hydroélectrique, forêt classée"),
    ("Mafèrè",            "MFR",   5.4833,  -3.4167, "Sous-préfecture du Sud-Comoé"),

    # ── Région de l'Indénié-Djuablin ──────────────────────────────────────────
    ("Abengourou",        "ABE",   6.7333,  -3.4833, "Chef-lieu de l'Indénié-Djuablin"),
    ("Agnibilékrou",      "AGN",   7.1333,  -3.2000, "Frontière est avec le Ghana"),
    ("Niablé",            "NIB",   6.5833,  -3.3500, "Sous-préfecture de l'Indénié-Djuablin"),
    ("Bettié",            "BTT",   6.9000,  -3.3167, "Frontière Ghana, région de l'Indénié"),
    ("Assié-Koumassi",    "ASK",   6.8167,  -3.6333, "Sous-préfecture de l'Indénié-Djuablin"),

    # ── Région du Gontougo ────────────────────────────────────────────────────
    ("Bondoukou",         "BDK",   8.0333,  -2.8000, "Mosquée historique, ancienne route de l'or"),
    ("Tanda",             "TDA",   7.8000,  -3.1667, "Région du Gontougo"),
    ("Koun-Fao",          "KFO",   7.3500,  -3.0167, "Sous-préfecture du Gontougo"),
    ("Sandégué",          "SDG",   8.3000,  -3.3167, "Sous-préfecture du Gontougo"),
    ("Transua",           "TRS",   7.7500,  -3.5000, "Sous-préfecture du Gontougo"),
    ("Tankessé",          "TNK",   8.5000,  -3.0333, "Sous-préfecture du Gontougo"),
    ("Kouassi-Datékro",   "KDT",   7.6333,  -3.7167, "Sous-préfecture du Gontougo"),

    # ── Région du Bounkani ────────────────────────────────────────────────────
    ("Bouna",             "BNA",   9.2667,  -3.0000, "Chef-lieu du Bounkani, parc de la Comoé"),
    ("Nassian",           "NAS",   8.9500,  -3.4667, "Sous-préfecture du Bounkani"),
    ("Doropo",            "DRP",  10.0667,  -3.3000, "Frontière Burkina Faso"),
    ("Téhini",            "TEH",   9.6500,  -3.6667, "Sous-préfecture du Bounkani"),
    ("Lolodou",           "LLD",   9.4167,  -3.2167, "Sous-préfecture du Bounkani"),

    # ── Région de l'Iffou ─────────────────────────────────────────────────────
    ("Dimbokro",          "DIM",   6.6500,  -4.7000, "Chef-lieu de l'Iffou"),
    ("Daoukro",           "DAO",   7.0667,  -3.9667, "Sous-préfecture de l'Iffou"),
    ("Bongouanou",        "BOG",   6.6500,  -4.2000, "Sous-préfecture de l'Iffou"),
    ("M'Bahiakro",        "MBH",   7.4500,  -4.3333, "Sous-préfecture de l'Iffou"),
    ("Prikro",            "PRK",   7.7000,  -4.5667, "Sous-préfecture de l'Iffou"),

    # ── Région du N'Zi ────────────────────────────────────────────────────────
    ("Bocanda",           "BOC",   7.0667,  -4.5167, "Chef-lieu du N'Zi"),
    ("Kouassi-Kouassikro","KKK",   7.2167,  -4.9500, "Sous-préfecture du N'Zi"),

    # ── Région du Bélier ──────────────────────────────────────────────────────
    ("Toumodi",           "TMD",   6.5500,  -5.0167, "Chef-lieu du Bélier"),
    ("Tiébissou",         "TIB",   7.1500,  -5.2333, "Sous-préfecture du Bélier"),
    ("Didiévi",           "DDV",   6.8833,  -5.3167, "Sous-préfecture du Bélier"),
    ("Djékanou",          "DJK",   6.6833,  -5.1167, "Sous-préfecture du Bélier"),
    ("Taabo-Village",     "TAV",   6.2000,  -5.0833, "Sous-préfecture du Bélier"),

    # ── Région du Gbêkê ───────────────────────────────────────────────────────
    ("Bouaké",            "BOU",   7.6833,  -5.0333, "Deuxième ville du pays, centre commercial"),
    ("Béoumi",            "BEO",   7.6667,  -5.5667, "Sous-préfecture du Gbêkê"),
    ("Botro",             "BTR",   7.8500,  -5.3000, "Sous-préfecture du Gbêkê"),
    ("Sakassou",          "SAK",   7.4500,  -5.3167, "Sous-préfecture du Gbêkê"),
    ("Djébonoua",         "DJB",   7.5167,  -5.2167, "Sous-préfecture du Gbêkê"),

    # ── Région du Hambol ──────────────────────────────────────────────────────
    ("Katiola",           "KAT",   8.1333,  -5.1000, "Chef-lieu du Hambol"),
    ("Niakara",           "NKR",   8.7500,  -5.2833, "Sous-préfecture du Hambol"),
    ("Dabakala",          "DBK",   8.3667,  -4.4333, "Sous-préfecture du Hambol"),
    ("Tafiré",            "TAF",   9.5167,  -5.0333, "Sous-préfecture du Hambol"),
    ("Tortiya",           "TRT",   8.9833,  -5.1167, "Zone minière diamantifère"),
    ("Niakaramadougou",   "NKM",   8.6500,  -5.2667, "Sous-préfecture du Hambol"),

    # ── Région du Gôh ─────────────────────────────────────────────────────────
    ("Gagnoa",            "GAG",   6.1333,  -5.9500, "Chef-lieu du Gôh"),
    ("Oumé",              "OUM",   6.3833,  -5.4167, "Sous-préfecture du Gôh"),
    ("Gnagbodougnoa",     "GNB",   5.9833,  -5.7167, "Sous-préfecture du Gôh"),
    ("Dignago",           "DGN",   6.3167,  -5.7500, "Sous-préfecture du Gôh"),
    ("Guibéroua",         "GBR2",  6.0500,  -6.0667, "Sous-préfecture du Gôh"),

    # ── Région de la Marahoué ─────────────────────────────────────────────────
    ("Bouaflé",           "BFL",   6.9833,  -5.7500, "Chef-lieu de la Marahoué"),
    ("Zuénoula",          "ZUE",   7.4333,  -6.0500, "Sous-préfecture de la Marahoué"),
    ("Sinfra",            "SIF",   6.6167,  -5.9167, "Sous-préfecture de la Marahoué"),
    ("Kounahiri",         "KNH",   7.9000,  -5.9500, "Sous-préfecture de la Marahoué"),
    ("Bonon",             "BNO",   7.2500,  -5.8833, "Sous-préfecture de la Marahoué"),

    # ── Région du Haut-Sassandra ──────────────────────────────────────────────
    ("Daloa",             "DAL",   6.8833,  -6.4500, "Chef-lieu du Haut-Sassandra"),
    ("Issia",             "ISS",   6.4833,  -6.5833, "Sous-préfecture du Haut-Sassandra"),
    ("Vavoua",            "VAV",   7.3833,  -6.4667, "Sous-préfecture du Haut-Sassandra"),
    ("Zoukougbeu",        "ZKB",   6.9167,  -6.9000, "Sous-préfecture du Haut-Sassandra"),
    ("Bogouiné",          "BGN",   7.1000,  -6.5333, "Sous-préfecture du Haut-Sassandra"),

    # ── Région du Worodougou ──────────────────────────────────────────────────
    ("Séguéla",           "SEG",   7.9667,  -6.6667, "Chef-lieu du Worodougou"),
    ("Koro",              "KRO",   8.5333,  -6.5667, "Sous-préfecture du Worodougou"),
    ("Massala",           "MSL",   8.0000,  -7.1333, "Sous-préfecture du Worodougou"),
    ("Worofla",           "WRF",   8.7167,  -6.7500, "Sous-préfecture du Worodougou"),
    ("Kamalo",            "KML",   8.2667,  -6.9833, "Sous-préfecture du Worodougou"),

    # ── Région du Béré ────────────────────────────────────────────────────────
    ("Mankono",           "MNK",   8.0583,  -6.1833, "Chef-lieu du Béré"),
    ("Kounahiri",         "KNR",   7.9000,  -5.9500, "Sous-préfecture du Béré"),
    ("Morondo",           "MRD",   9.0333,  -6.7500, "Sous-préfecture du Béré"),
    ("Dianra",            "DNR",   9.1667,  -6.3167, "Sous-préfecture du Béré"),
    ("Dianra-Village",    "DNV",   9.2167,  -6.2833, "Sous-préfecture du Béré"),

    # ── Région du Bafing ──────────────────────────────────────────────────────
    ("Touba",             "TBA",   8.2833,  -7.6833, "Chef-lieu du Bafing"),
    ("Ouaninou",          "OUA",   8.0333,  -7.9000, "Sous-préfecture du Bafing"),
    ("Koro",              "KRB",   8.4333,  -7.5167, "Sous-préfecture du Bafing"),
    ("Bogolo",            "BGL",   8.6667,  -7.2333, "Sous-préfecture du Bafing"),

    # ── Région du Kabadougou ──────────────────────────────────────────────────
    ("Odienné",           "ODI",   9.5000,  -7.5667, "Chef-lieu du Kabadougou"),
    ("Gbéléban",          "GBL",   9.6167,  -8.2000, "Sous-préfecture du Kabadougou"),
    ("Samatiguila",       "SMT",   9.8833,  -7.8167, "Sous-préfecture du Kabadougou"),
    ("Madinani",          "MDN",   9.5833,  -7.2667, "Sous-préfecture du Kabadougou"),
    ("Séguélon",          "SGL",   9.5000,  -6.2667, "Sous-préfecture du Kabadougou"),

    # ── Région du Folon ───────────────────────────────────────────────────────
    ("Minignan",          "MGN",  10.4167,  -7.9667, "Chef-lieu du Folon, frontière Mali/Guinée"),
    ("Kaniasso",          "KAN",   9.8833,  -8.1500, "Sous-préfecture du Folon"),

    # ── Région du Poro ────────────────────────────────────────────────────────
    ("Korhogo",           "KOR",   9.4500,  -5.6333, "Chef-lieu du Poro, capitale du nord"),
    ("Dikodougou",        "DKD",   9.0667,  -5.7833, "Sous-préfecture du Poro"),
    ("Mbengué",           "MBG",   9.9833,  -5.7667, "Sous-préfecture du Poro, frontière Mali"),
    ("Sinématiali",       "SNM",   9.6167,  -5.3833, "Sous-préfecture du Poro"),
    ("Napié",             "NAP",   9.2167,  -5.5667, "Sous-préfecture du Poro"),
    ("Kiémou",            "KIM",   9.3000,  -5.9000, "Sous-préfecture du Poro"),
    ("Kasséré",           "KSR",   9.2500,  -5.3333, "Sous-préfecture du Poro"),

    # ── Région du Tchologo ────────────────────────────────────────────────────
    ("Ferkessédougou",    "FER",   9.5833,  -5.2000, "Chef-lieu du Tchologo, industrie sucrière"),
    ("Kong",              "KNG",   9.1500,  -4.6167, "Ancienne cité historique, mosquée en banco"),
    ("Ouangolodougou",    "OGL",   9.9833,  -5.1500, "Sous-préfecture du Tchologo, frontière Burkina"),
    ("Niellé",            "NEL",  10.2000,  -5.6333, "Sous-préfecture du Tchologo"),
    ("Dialecte Kombolokoura", "KBK", 9.4833, -4.9167, "Sous-préfecture du Tchologo"),

    # ── Région de la Bagoué ───────────────────────────────────────────────────
    ("Boundiali",         "BDI",   9.5167,  -6.4833, "Chef-lieu de la Bagoué"),
    ("Tingréla",          "TIN",  10.4833,  -6.1333, "Frontière nord avec le Mali"),
    ("Kouto",             "KTO",   9.8833,  -6.4167, "Sous-préfecture de la Bagoué"),
    ("Gbon",              "GBN",   9.9667,  -6.5667, "Sous-préfecture de la Bagoué"),
    ("Boundiali-Rural",   "BDR",   9.5500,  -6.5167, "Sous-préfecture de la Bagoué"),

    # ── Région du Lôh-Djiboua ─────────────────────────────────────────────────
    ("Divo",              "DIV",   5.8333,  -5.3667, "Chef-lieu du Lôh-Djiboua"),
    ("Lakota",            "LAK",   5.8500,  -5.6833, "Sous-préfecture du Lôh-Djiboua"),
    ("Guitry",            "GTR",   5.4500,  -5.5000, "Sous-préfecture du Lôh-Djiboua"),
    ("Grand-Zattry",      "GZT",   5.4833,  -5.6500, "Sous-préfecture du Lôh-Djiboua"),
    ("Hiré",              "HIR",   5.7167,  -5.6833, "Zone minière aurifère"),
    ("Rubino",            "RUB",   5.9167,  -5.2000, "Sous-préfecture du Lôh-Djiboua"),
    ("Gueyo",             "GYO",   5.5500,  -5.8333, "Sous-préfecture du Lôh-Djiboua"),
    ("Doa",               "DOA",   5.6333,  -5.3333, "Sous-préfecture du Lôh-Djiboua"),

    # ── Région de la Nawa ─────────────────────────────────────────────────────
    ("Soubré",            "SOU",   5.7833,  -6.6000, "Chef-lieu de la Nawa, zone cacaoyère"),
    ("Méagui",            "MEA",   5.4167,  -6.9833, "Sous-préfecture de la Nawa"),
    ("Buyo",              "BUY",   6.2500,  -7.0333, "Lac de Buyo, barrage hydroélectrique"),
    ("Guéyo",             "GUY",   5.5333,  -6.8500, "Sous-préfecture de la Nawa"),
    ("Okrouyo",           "OKR",   5.6833,  -6.7167, "Sous-préfecture de la Nawa"),

    # ── Région du San-Pédro ───────────────────────────────────────────────────
    ("San-Pédro",         "SAN",   4.7500,  -6.6333, "Port économique du sud-ouest"),
    ("Sassandra",         "SAS",   4.9500,  -6.0833, "Port de pêche historique"),
    ("Fresco",            "FRE",   5.0500,  -5.5667, "Côte balnéaire, pêche artisanale"),
    ("Grand-Béréby",      "GBR",   4.6333,  -6.9000, "Côte balnéaire sud-ouest"),
    ("Tabou",             "TAB",   4.4167,  -7.3500, "Frontière avec le Liberia"),
    ("Grabo",             "GRB",   4.9667,  -7.4333, "Frontière Liberia, région du San-Pédro"),
    ("Meadji",            "MDJ",   4.5333,  -7.5167, "Sous-préfecture du San-Pédro"),
    ("Drewin",            "DRW",   5.0000,  -6.3500, "Côte balnéaire du San-Pédro"),
    ("Gabiadji",          "GBD",   4.7333,  -6.3500, "Sous-préfecture du San-Pédro"),

    # ── Région du Guémon ──────────────────────────────────────────────────────
    ("Guiglo",            "GUI",   6.5333,  -7.4833, "Chef-lieu du Guémon"),
    ("Duekoué",           "DUE",   6.7333,  -7.3500, "Sous-préfecture du Guémon"),
    ("Bangolo",           "BAG",   7.0167,  -7.4833, "Sous-préfecture du Guémon"),
    ("Bloléquin",         "BLQ",   6.4667,  -8.0000, "Sous-préfecture du Guémon"),
    ("Kouibly",           "KBL",   7.0667,  -7.7167, "Sous-préfecture du Guémon"),
    ("Facobly",           "FCB",   7.3833,  -7.9500, "Sous-préfecture du Guémon"),
    ("Taï",               "TAI",   5.8667,  -7.4500, "Parc national de Taï, patrimoine UNESCO"),
    ("Zéo",               "ZEO",   6.7167,  -7.6500, "Sous-préfecture du Guémon"),

    # ── Région du Tonkpi ──────────────────────────────────────────────────────
    ("Man",               "MAN",   7.4125,  -7.5539, "Chef-lieu du Tonkpi, ville des montagnes"),
    ("Danané",            "DAN",   7.2667,  -8.1500, "Frontière Guinée, région du Tonkpi"),
    ("Biankouma",         "BIA",   7.7333,  -7.6167, "Sous-préfecture du Tonkpi"),
    ("Zouan-Hounien",     "ZOH",   6.9167,  -8.3333, "Sous-préfecture du Tonkpi"),
    ("Sipilou",           "SPL",   8.0000,  -7.9667, "Frontière Guinée, région du Tonkpi"),
    ("Toulepleu",         "TLP",   6.5833,  -8.4000, "Frontière avec le Liberia"),
    ("Logoualé",          "LGL",   7.1167,  -7.7167, "Sous-préfecture du Tonkpi"),
    ("Bin-Houyé",         "BNH",   7.2833,  -7.8333, "Sous-préfecture du Tonkpi"),
]

# Déduplication sur le code
_seen_codes = set()
_deduped    = []
for _entry in COTE_IVOIRE_VILLES:
    if _entry[1] not in _seen_codes:
        _seen_codes.add(_entry[1])
        _deduped.append(_entry)
COTE_IVOIRE_VILLES = _deduped


# ─── Mapping OSM → champs Django ──────────────────────────────────────────────

OSM_SURFACE_MAP = {
    "asphalt": "bitume", "paved": "bitume", "concrete": "bitume",
    "cobblestone": "pave", "sett": "pave", "paving_stones": "pave",
    "unpaved": "terre", "dirt": "terre", "earth": "terre", "mud": "terre",
    "gravel": "gravier", "fine_gravel": "gravier", "compacted": "gravier",
}

HIGHWAY_SURFACE_FALLBACK = {
    "motorway": "bitume", "trunk": "bitume", "primary": "bitume",
    "secondary": "bitume", "tertiary": "bitume", "residential": "bitume",
    "service": "bitume", "unclassified": "terre", "track": "terre",
    "path": "terre", "footway": "terre",
}

HIGHWAY_BASE_SCORE = {
    "motorway": 88, "trunk": 82, "primary": 75, "secondary": 65,
    "tertiary": 55, "residential": 58, "service": 52,
    "unclassified": 38, "track": 28, "path": 22, "footway": 18,
}

HIGHWAY_LABEL = {
    "motorway": "Autoroute", "trunk": "Route nationale",
    "primary": "Route principale", "secondary": "Route secondaire",
    "tertiary": "Route tertiaire", "residential": "Voie résidentielle",
    "unclassified": "Route non classée", "track": "Piste",
    "path": "Chemin", "footway": "Sentier", "service": "Voie de service",
}

SMOOTHNESS_DELTA = {
    "excellent": +15, "good": +8, "intermediate": 0,
    "bad": -15, "very_bad": -25, "horrible": -35, "impassable": -50,
}

FLOOD_SCORE_RANGE = {
    "river": (60, 85), "canal": (55, 78), "stream": (35, 60),
    "wetland": (42, 72), "water": (25, 55),
}

NDVI_RANGE = {
    "forest": (0.62, 0.88), "wood": (0.60, 0.86),
    "orchard": (0.48, 0.72), "grass": (0.28, 0.55),
    "meadow": (0.30, 0.58), "grassland": (0.28, 0.52),
    "scrub": (0.22, 0.48), "heath": (0.18, 0.42),
    "farmland": (0.15, 0.40),
}

HIGHWAY_TAGS        = "motorway|trunk|primary|secondary|tertiary|residential|unclassified|track"
WATER_NATURAL_TAGS  = "wetland|water"
WATER_WAY_TAGS      = "river|stream|canal"
LANDUSE_TAGS        = "forest|grass|meadow|orchard|farmland"
NATURAL_VEG_TAGS    = "wood|scrub|grassland|heath"


# ─── Helpers géographiques ────────────────────────────────────────────────────

def make_bbox(lat: float, lng: float, r: float = SEARCH_RADIUS) -> str:
    return f"{lat - r},{lng - r},{lat + r},{lng + r}"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def surface_from_tags(highway: str, tags: dict) -> str:
    return (OSM_SURFACE_MAP.get(tags.get("surface", ""))
            or HIGHWAY_SURFACE_FALLBACK.get(highway, "autre"))


def score_from_tags(highway: str, tags: dict) -> int:
    score = HIGHWAY_BASE_SCORE.get(highway, 40)
    score += SMOOTHNESS_DELTA.get(tags.get("smoothness", ""), 0)
    if tags.get("surface") in ("paved", "asphalt", "concrete"):
        score = max(score, 55)
    elif tags.get("surface") in ("unpaved", "dirt", "mud"):
        score = min(score, 45)
    return max(5, min(100, score))


def status_from_score(score: int) -> str:
    if score >= 70: return "bon"
    if score >= 45: return "degrade"
    if score >= 20: return "critique"
    return "ferme"


def ndvi_to_density(ndvi: float) -> str:
    if ndvi < 0.2: return "sparse"
    if ndvi < 0.4: return "moderate"
    if ndvi < 0.6: return "dense"
    return "very_dense"


def _close_polygon(coords: list) -> list:
    """Ferme un anneau de polygone si le premier et le dernier point diffèrent."""
    if coords and coords[0] != coords[-1]:
        return coords + [coords[0]]
    return coords


def _polygon_area_km2(geometry: list) -> float:
    """
    Approximation de l'aire réelle d'un polygone OSM via la formule de Shoelace
    projetée en coordonnées métriques (approximation plate valide sur de petites surfaces).
    Beaucoup plus précis que haversine(bbox) * 0.5 pour les formes irrégulières.
    """
    if len(geometry) < 3:
        return 0.0
    # Centroïde pour la projection locale
    lat0 = sum(p["lat"] for p in geometry) / len(geometry)
    cos_lat = math.cos(math.radians(lat0))
    # Conversion degrés → mètres (approximation locale)
    R = 6_371_000.0
    pts = [
        (math.radians(p["lon"]) * R * cos_lat,
         math.radians(p["lat"]) * R)
        for p in geometry
    ]
    # Formule de Shoelace
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return round(abs(area) / 2.0 / 1_000_000, 4)  # m² → km²


# ─── Overpass : rotation d'instances et backoff adaptatif ─────────────────────

def _next_overpass_instance() -> str:
    global _overpass_instance_index
    _overpass_instance_index = (_overpass_instance_index + 1) % len(OVERPASS_INSTANCES)
    return OVERPASS_INSTANCES[_overpass_instance_index]


def overpass_fetch(zone_bbox: str, roads_only: bool, stdout) -> dict | None:
    global _consecutive_429

    if roads_only:
        query = f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:100000000];
        (
          way["highway"~"^({HIGHWAY_TAGS})$"]({zone_bbox});
        );
        out body geom {MAX_ELEMENTS};
        """
    else:
        query = f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:100000000];
        (
          way["highway"~"^({HIGHWAY_TAGS})$"]({zone_bbox});
          way["natural"~"^({WATER_NATURAL_TAGS})$"]({zone_bbox});
          way["waterway"~"^({WATER_WAY_TAGS})$"]({zone_bbox});
          way["landuse"~"^({LANDUSE_TAGS})$"]({zone_bbox});
          way["natural"~"^({NATURAL_VEG_TAGS})$"]({zone_bbox});
        );
        out body geom {MAX_ELEMENTS};
        """

    for attempt in range(1, MAX_RETRIES + 1):
        url = OVERPASS_INSTANCES[_overpass_instance_index]
        try:
            resp = requests.post(
                url,
                data={"data": query},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "GéoDash/1.0 (contact@geodash-ci.example.com)"},
            )
            resp.raise_for_status()
            data = resp.json()

            if "remark" in data and "error" in data.get("remark", "").lower():
                raise ValueError(f"Overpass remark: {data['remark']}")

            _consecutive_429 = 0
            return data

        except requests.exceptions.Timeout:
            msg = f"Timeout Overpass (tentative {attempt}/{MAX_RETRIES}) sur {url}"
            logger.warning(msg)
            stdout.write(f"  {msg}")
            _next_overpass_instance()

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code == 429:
                _consecutive_429 += 1
                retry_after = e.response.headers.get("Retry-After")
                if retry_after:
                    wait = int(retry_after)
                    source = "Retry-After header"
                else:
                    wait = min(60 * (2 ** (attempt - 1)), _MAX_GLOBAL_WAIT)
                    source = "backoff exponentiel"
                msg = (f"HTTP 429 (tentative {attempt}/{MAX_RETRIES}) — "
                       f"attente {wait}s ({source})")
                logger.warning(msg)
                stdout.write(f"  {msg}")
                new_url = _next_overpass_instance()
                stdout.write(f"  Bascule vers {new_url}")
                time.sleep(wait)
                continue
            else:
                msg = f"Erreur HTTP {code} (tentative {attempt}/{MAX_RETRIES})"
                logger.warning(msg)
                stdout.write(f"  {msg}")

        except requests.exceptions.RequestException as e:
            msg = f"Erreur réseau : {e} (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  {msg}")
            _next_overpass_instance()

        except ValueError as e:
            logger.error("Réponse Overpass invalide : %s", e)
            stdout.write(f"  Réponse invalide : {e}")
            return None

        if attempt < MAX_RETRIES:
            wait = RETRY_DELAY * attempt
            stdout.write(f"  Nouvelle tentative dans {wait}s...")
            time.sleep(wait)

    logger.error("Overpass inaccessible après %d tentatives", MAX_RETRIES)
    return None


def _inter_zone_delay(stdout) -> None:
    """Pause adaptative — s'allonge si le serveur throttle en série."""
    if _consecutive_429 == 0:
        wait = REQUEST_DELAY
    elif _consecutive_429 <= 3:
        wait = REQUEST_DELAY * 5
    elif _consecutive_429 <= 6:
        wait = REQUEST_DELAY * 10
    else:
        wait = _MAX_GLOBAL_WAIT
        stdout.write(
            f"  {_consecutive_429} zones en 429 consecutif — "
            f"pause de {wait}s pour laisser le serveur recuperer."
        )
    time.sleep(wait)


# ─── Classificateur d'éléments OSM ───────────────────────────────────────────

def classify_element(el: dict) -> str:
    tags = el.get("tags", {})
    if tags.get("highway") in HIGHWAY_TAGS.split("|"):
        return "road"
    if tags.get("waterway") in WATER_WAY_TAGS.split("|"):
        return "flood"
    if tags.get("natural") in WATER_NATURAL_TAGS.split("|"):
        return "flood"
    if tags.get("landuse") in LANDUSE_TAGS.split("|"):
        return "vegetation"
    if tags.get("natural") in NATURAL_VEG_TAGS.split("|"):
        return "vegetation"
    return None


# ─── Création automatique des zones ──────────────────────────────────────────

def create_zones_if_missing(stdout) -> int:
    existing = set(Zone.objects.values_list("code", flat=True))
    to_create = [
        Zone(name=n, code=c, lat_center=lat, lng_center=lng, description=d)
        for n, c, lat, lng, d in COTE_IVOIRE_VILLES
        if c not in existing
    ]
    if to_create:
        Zone.objects.bulk_create(to_create)
        for z in to_create:
            stdout.write(f"  + {z.name} ({z.code})")
    return len(to_create)


# ─── Sauvegarde en base — fonction générique DRY ─────────────────────────────
#
# Les 3 fonctions save_* sont factorisées ici.
# Clé d'identification : osm_id (BigIntegerField dans les modèles).
# Avantages vs l'ancienne clé par nom :
#   - Pas de collision sur les noms identiques ("Route Nationale" × 50)
#   - Suppression automatique des éléments disparus d'OSM
#   - Gestion d'erreur par élément sans planter tout le lot
#
# PRÉREQUIS : osm_id doit exister dans le modèle.
# Voir models_patch.py pour le diff à appliquer.

def _build_road_defaults(el: dict, now) -> dict | None:
    """Construit le dict de champs pour un RoadSegment depuis un élément OSM."""
    tags    = el.get("tags", {})
    highway = tags.get("highway", "unclassified")
    name    = (tags.get("name") or tags.get("ref")
               or f"{HIGHWAY_LABEL.get(highway, highway)} #{el['id']}")
    geometry = el.get("geometry", [])
    if len(geometry) < 2:
        return None  # trop peu de points, segment invalide

    geojson = {
        "type": "LineString",
        "coordinates": [[p["lon"], p["lat"]] for p in geometry],
    }
    score   = score_from_tags(highway, tags)
    status  = status_from_score(score)
    surface = surface_from_tags(highway, tags)

    parts = [f"Type OSM : {highway}"]
    if tags.get("maxspeed"):   parts.append(f"Vitesse max : {tags['maxspeed']} km/h")
    if tags.get("lanes"):      parts.append(f"Voies : {tags['lanes']}")
    if tags.get("smoothness"): parts.append(f"État OSM : {tags['smoothness']}")

    return {
        "name":            name,
        "status":          status,
        "condition_score": score,
        "surface_type":    surface,
        "geojson":         geojson,
        "notes":           " | ".join(parts),
        "last_analyzed":   now,
    }


def _build_flood_defaults(el: dict, now) -> dict | None:
    """Construit le dict de champs pour un FloodRisk depuis un élément OSM."""
    tags     = el.get("tags", {})
    waterway = tags.get("waterway", "")
    natural  = tags.get("natural", "")
    name     = tags.get("name") or tags.get("ref") or f"Zone hydro #{el['id']}"
    key      = waterway or natural

    lo, hi = FLOOD_SCORE_RANGE.get(key, (20, 50))
    score  = round(lo + (el["id"] % 1000) / 1000.0 * (hi - lo), 1)

    if score >= 70:   risk_level = "critique"
    elif score >= 50: risk_level = "eleve"
    elif score >= 30: risk_level = "modere"
    else:             risk_level = "faible"

    geometry = el.get("geometry", [])
    if geometry:
        coords   = [[p["lon"], p["lat"]] for p in geometry]
        closed   = _close_polygon(coords)  # fermeture explicite du polygone
        area_km2 = _polygon_area_km2(geometry)
        geojson  = {"type": "Polygon", "coordinates": [closed]}
    else:
        area_km2 = 0.0
        geojson  = {}

    return {
        "name":         name,
        "risk_level":   risk_level,
        "risk_score":   score,
        "area_km2":     area_km2,
        "rainfall_mm":  0.0,
        "geojson":      geojson,
        "last_analyzed": now,
    }


def _build_vegetation_defaults(el: dict, now) -> dict | None:
    """Construit le dict de champs pour un VegetationDensity depuis un élément OSM."""
    tags    = el.get("tags", {})
    landuse = tags.get("landuse", "")
    natural = tags.get("natural", "")
    name    = tags.get("name") or f"Végétation #{el['id']}"
    key     = landuse or natural

    lo, hi = NDVI_RANGE.get(key, (0.18, 0.55))
    ndvi   = round(lo + (el["id"] % 10000) / 10000.0 * (hi - lo), 3)
    density = ndvi_to_density(ndvi)

    geometry = el.get("geometry", [])
    if geometry:
        coords  = [[p["lon"], p["lat"]] for p in geometry]
        closed  = _close_polygon(coords)
        geojson = {"type": "Polygon", "coordinates": [closed]}
    else:
        geojson = {}

    return {
        "name":              name,
        "ndvi_value":        ndvi,
        "density_class":     density,
        "coverage_percent":  round(ndvi * 100, 1),
        "change_vs_previous": 0.0,
        "geojson":           geojson,
        "last_analyzed":     now,
    }


# Correspondance modèle → builder + champs à mettre à jour
_MODEL_CONFIG = {
    RoadSegment: {
        "builder":       _build_road_defaults,
        "update_fields": ["name", "condition_score", "status", "surface_type",
                          "geojson", "notes", "last_analyzed"],
    },
    FloodRisk: {
        "builder":       _build_flood_defaults,
        "update_fields": ["name", "risk_level", "risk_score", "area_km2",
                          "geojson", "last_analyzed"],
    },
    VegetationDensity: {
        "builder":       _build_vegetation_defaults,
        "update_fields": ["name", "ndvi_value", "density_class",
                          "coverage_percent", "geojson", "last_analyzed"],
    },
}


def _save_elements(
    zone: Zone,
    elements: list,
    ModelClass,
    stdout,
) -> tuple[int, int, int]:
    """
    Upsert + delete générique pour RoadSegment, FloodRisk, VegetationDensity.

    Retourne (créés, mis_à_jour, supprimés).

    Identification par osm_id — pas de collision sur les noms.
    Les éléments disparus d'OSM sont supprimés de la base.
    Les erreurs par élément sont loggées sans planter le lot.
    """
    cfg    = _MODEL_CONFIG[ModelClass]
    builder = cfg["builder"]
    update_fields = cfg["update_fields"]
    now = timezone.now()

    # Charger les objets existants indexés par osm_id
    existing: dict[int, ModelClass] = {
        obj.osm_id: obj
        for obj in ModelClass.objects.filter(zone=zone).only("id", "osm_id", *update_fields)
        if obj.osm_id is not None
    }

    to_create:     list = []
    to_update:     list = []
    processed_ids: set  = set()
    skipped        = 0

    for el in elements:
        osm_id = el.get("id")
        if osm_id is None:
            skipped += 1
            continue

        try:
            defaults = builder(el, now)
            if defaults is None:
                # Géométrie invalide (ex: segment < 2 points)
                skipped += 1
                continue

            processed_ids.add(osm_id)

            if osm_id in existing:
                obj = existing[osm_id]
                for field, value in defaults.items():
                    setattr(obj, field, value)
                to_update.append(obj)
            else:
                to_create.append(
                    ModelClass(zone=zone, osm_id=osm_id, **defaults)
                )

        except Exception as e:
            logger.error(
                "Erreur traitement OSM id=%s (%s / zone=%s) : %s",
                osm_id, ModelClass.__name__, zone.code, e
            )
            skipped += 1
            continue

    # Éléments disparus d'OSM depuis le dernier import
    obsolete_ids = set(existing.keys()) - processed_ids

    with transaction.atomic():
        if to_create:
            ModelClass.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            ModelClass.objects.bulk_update(to_update, update_fields, batch_size=200)
        if obsolete_ids:
            deleted_count, _ = ModelClass.objects.filter(
                zone=zone, osm_id__in=obsolete_ids
            ).delete()
            logger.info(
                "%d %s supprimés (obsolètes OSM) — zone %s",
                deleted_count, ModelClass.__name__, zone.code
            )

    if skipped:
        logger.debug("%d éléments ignorés (géométrie invalide) — zone %s", skipped, zone.code)

    return len(to_create), len(to_update), len(obsolete_ids)


# ─── Fonctions publiques (conservent la même interface qu'avant) ──────────────

def save_roads(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    c, u, d = _save_elements(zone, elements, RoadSegment, stdout)
    if d:
        stdout.write(f"  Routes : {d} obsolètes supprimées")
    return c, u


def save_flood_risks(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    c, u, d = _save_elements(zone, elements, FloodRisk, stdout)
    if d:
        stdout.write(f"  Inondations : {d} obsolètes supprimées")
    return c, u


def save_vegetation(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    c, u, d = _save_elements(zone, elements, VegetationDensity, stdout)
    if d:
        stdout.write(f"  Végétation : {d} obsolètes supprimées")
    return c, u


# ─── Génération d'alertes ─────────────────────────────────────────────────────

def generate_alerts(zone: Zone) -> int:
    """
    Génère des alertes pour les routes dégradées et les zones inondables critiques.
    get_or_create évite les doublons à chaque réimport.
    """
    count = 0
    now   = timezone.now()

    for road in (zone.roads
                 .filter(status__in=["critique", "ferme"])
                 .order_by("condition_score")[:3]):
        _, created = Alert.objects.get_or_create(
            zone=zone,
            title=f"Route dégradée : {road.name}",
            category="road",
            is_read=False,
            defaults={
                "message": (
                    f"Segment '{road.name}' — score {road.condition_score}/100 "
                    f"({road.get_status_display()}). Inspection recommandée."
                ),
                "severity":   "critical" if road.status == "ferme" else "danger",
                "created_at": now,
                "lat":        zone.lat_center,
                "lng":        zone.lng_center,
            },
        )
        if created:
            count += 1

    for flood in (zone.flood_risks
                  .filter(risk_level__in=["eleve", "critique"])
                  .order_by("-risk_score")[:2]):
        _, created = Alert.objects.get_or_create(
            zone=zone,
            title=f"Risque inondation : {flood.name}",
            category="flood",
            is_read=False,
            defaults={
                "message": (
                    f"Zone '{flood.name}' — risque {flood.get_risk_level_display()}, "
                    f"score {flood.risk_score}/100."
                ),
                "severity":   "critical" if flood.risk_level == "critique" else "warning",
                "created_at": now,
                "lat":        zone.lat_center,
                "lng":        zone.lng_center,
            },
        )
        if created:
            count += 1

    return count


# ─── Commande Django ──────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Importe les données géospatiales OSM pour les zones de Côte d'Ivoire."

    def add_arguments(self, parser):
        parser.add_argument(
            "--zone", type=str, default=None,
            help="Code zone (ex: MAN). Absent = toutes les zones.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Simule sans écrire en base.",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Supprime les données existantes avant import.",
        )
        parser.add_argument(
            "--roads-only", action="store_true",
            help="Routes uniquement, ignore eau et végétation.",
        )

    def handle(self, *args, **options):
        dry_run    = options["dry_run"]
        zone_code  = options["zone"]
        roads_only = options["roads_only"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN -- aucune ecriture en base\n"))

        if not dry_run:
            self.stdout.write(self.style.HTTP_INFO("Verification des zones de Cote d'Ivoire..."))
            n = create_zones_if_missing(self.stdout)
            if n:
                self.stdout.write(self.style.SUCCESS(f"  {n} zone(s) creee(s)\n"))
            else:
                self.stdout.write(f"  {Zone.objects.count()} zones deja presentes\n")

        if zone_code:
            zones = Zone.objects.filter(code__iexact=zone_code)
            if not zones.exists():
                available = ", ".join(Zone.objects.values_list("code", flat=True))
                raise CommandError(
                    f"Zone '{zone_code}' introuvable. Codes disponibles : {available}"
                )
        else:
            zones = Zone.objects.all()

        if not zones.exists():
            raise CommandError(
                "Aucune zone en base. Lance sans --zone pour créer les zones CI."
            )

        self.stdout.write(f"Zones a traiter : {zones.count()}\n")
        self.stdout.write(f"Instances Overpass : {', '.join(OVERPASS_INSTANCES)}\n")

        if options["clear"] and not dry_run:
            self.stdout.write(self.style.WARNING("Suppression des donnees existantes..."))
            with transaction.atomic():
                if zone_code:
                    for z in zones:
                        z.roads.all().delete()
                        z.flood_risks.all().delete()
                        z.vegetation.all().delete()
                        z.alerts.all().delete()
                else:
                    RoadSegment.objects.all().delete()
                    FloodRisk.objects.all().delete()
                    VegetationDensity.objects.all().delete()
                    Alert.objects.filter(
                        category__in=["road", "flood", "vegetation"]
                    ).delete()
            self.stdout.write("  Base nettoyee\n")

        totals = dict(rc=0, ru=0, fc=0, fu=0, vc=0, vu=0, alerts=0, errors=0)

        for zone in zones:
            self.stdout.write(self.style.HTTP_INFO(f"\n{'-' * 52}"))
            self.stdout.write(self.style.HTTP_INFO(f"  {zone.name} ({zone.code})"))

            zone_bbox = make_bbox(zone.lat_center, zone.lng_center)
            self.stdout.write("  Requete Overpass (routes + eau + vegetation)...")
            data = overpass_fetch(zone_bbox, roads_only, self.stdout)

            if data is None:
                totals["errors"] += 1
                logger.error("Import echoue -- zone %s (%s)", zone.name, zone.code)
                self.stdout.write(self.style.ERROR(
                    f"  Overpass inaccessible pour {zone.name} -- zone ignoree."
                ))
                _inter_zone_delay(self.stdout)
                continue

            elements = [el for el in data.get("elements", []) if el.get("type") == "way"]
            roads_el = [el for el in elements if classify_element(el) == "road"]
            flood_el = [el for el in elements if classify_element(el) == "flood"]
            veg_el   = [el for el in elements if classify_element(el) == "vegetation"]

            self.stdout.write(
                f"  {len(roads_el)} routes, {len(flood_el)} zones eau, "
                f"{len(veg_el)} zones vegetation"
            )

            if dry_run:
                _inter_zone_delay(self.stdout)
                continue

            try:
                c, u = save_roads(zone, roads_el, self.stdout)
                totals["rc"] += c
                totals["ru"] += u
                self.stdout.write(f"  Routes : {c} creees, {u} mises a jour")
            except Exception as e:
                totals["errors"] += 1
                logger.exception("Erreur save_roads -- zone %s", zone.code)
                self.stdout.write(self.style.ERROR(f"  Routes KO : {e}"))

            if not roads_only:
                try:
                    c, u = save_flood_risks(zone, flood_el, self.stdout)
                    totals["fc"] += c
                    totals["fu"] += u
                    self.stdout.write(f"  Inondations : {c} creees, {u} mises a jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_flood_risks -- zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Inondations KO : {e}"))

                try:
                    c, u = save_vegetation(zone, veg_el, self.stdout)
                    totals["vc"] += c
                    totals["vu"] += u
                    self.stdout.write(f"  Vegetation : {c} creees, {u} mises a jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_vegetation -- zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Vegetation KO : {e}"))

                try:
                    n = generate_alerts(zone)
                    totals["alerts"] += n
                    self.stdout.write(f"  Alertes generees : {n}")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur generate_alerts -- zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Alertes KO : {e}"))

            _inter_zone_delay(self.stdout)

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 52}"))
        self.stdout.write(self.style.SUCCESS("Import termine"))
        if not dry_run:
            self.stdout.write(
                f"  Routes      -- creees : {totals['rc']}, mises a jour : {totals['ru']}"
            )
            self.stdout.write(
                f"  Inondations -- creees : {totals['fc']}, mises a jour : {totals['fu']}"
            )
            self.stdout.write(
                f"  Vegetation  -- creees : {totals['vc']}, mises a jour : {totals['vu']}"
            )
            self.stdout.write(f"  Alertes generees     : {totals['alerts']}")
            if totals["errors"]:
                self.stdout.write(self.style.WARNING(
                    f"  {totals['errors']} zone(s) en erreur -- voir les logs Django."
                ))
        else:
            self.stdout.write(self.style.WARNING(
                "\nDry-run termine. Relance sans --dry-run pour importer reellement."
            ))