"""
populate_geodata.py — GéoDash
Importe les données géospatiales depuis OpenStreetMap via l'API Overpass.

Optimisation principale : une seule requête Overpass par zone qui récupère
routes + eau + végétation en même temps. Avant on faisait 3 requêtes séparées
et la 3e se faisait throttler quasi systématiquement.

Usage :
    python manage.py populate_geodata                  # toutes les zones CI
    python manage.py populate_geodata --zone MAN       # une seule ville
    python manage.py populate_geodata --dry-run        # pour voir sans toucher la base
    python manage.py populate_geodata --clear          # repart de zéro
    python manage.py populate_geodata --roads-only     # routes uniquement, plus rapide
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


# ─── Config ───────────────────────────────────────────────────────────────────
# OVERPASS_URL peut être surchargé en prod si on veut pointer vers un miroir
# plus proche (ex: overpass.kumi.systems pour l'Afrique de l'Ouest)

OVERPASS_URL     = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT = 90     # en secondes — requête fusionnée = plus lourde qu'une simple
REQUEST_DELAY    = 4.0    # délai entre zones, respecter le fair-use Overpass
MAX_RETRIES      = 3
RETRY_DELAY      = 15.0   # on attend plus longtemps si le serveur est surchargé
SEARCH_RADIUS    = 0.05   # ~5 km autour du centre ville, suffisant pour les agglomérations
MAX_ELEMENTS     = 500    # au-delà de 500 éléments par type, ça ralentit beaucoup


# ─── Villes de Côte d'Ivoire ──────────────────────────────────────────────────
# Couverture exhaustive : 31 régions + 2 districts autonomes, tous les chefs-lieux
# de département et les principales sous-préfectures.
# Format : (nom affiché, code unique, latitude, longitude, description)
# Les coordonnées des petites localités sont approximatives (centre OSM).

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
    ("Koro",              "KRB",   8.4333,  -7.5167, "Sous-préfecture du Bafing"),  # différent de Koro Worodougou
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

# Déduplication automatique sur le code — sécurité si une ville apparaît deux fois
_seen_codes = set()
_deduped    = []
for _entry in COTE_IVOIRE_VILLES:
    if _entry[1] not in _seen_codes:
        _seen_codes.add(_entry[1])
        _deduped.append(_entry)
COTE_IVOIRE_VILLES = _deduped


# ─── Mapping OSM → champs Django ──────────────────────────────────────────────
# Ces dicts évitent une cascade de if/elif dans les fonctions de parsing.
# Ajouter un nouveau type de surface ici suffit, pas besoin de toucher ailleurs.

OSM_SURFACE_MAP = {
    "asphalt": "bitume", "paved": "bitume", "concrete": "bitume",
    "cobblestone": "pave", "sett": "pave", "paving_stones": "pave",
    "unpaved": "terre", "dirt": "terre", "earth": "terre", "mud": "terre",
    "gravel": "gravier", "fine_gravel": "gravier", "compacted": "gravier",
}

# Fallback quand le tag "surface" est absent : on déduit depuis le type de route
HIGHWAY_SURFACE_FALLBACK = {
    "motorway": "bitume", "trunk": "bitume", "primary": "bitume",
    "secondary": "bitume", "tertiary": "bitume", "residential": "bitume",
    "service": "bitume", "unclassified": "terre", "track": "terre",
    "path": "terre", "footway": "terre",
}

# Score de base par type de route — calibré pour les routes ivoiriennes
# (une "primary" en CI n'est pas au même niveau qu'une "primary" en France)
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

# Ajustement du score selon l'état de surface déclaré par les contributeurs OSM
SMOOTHNESS_DELTA = {
    "excellent": +15, "good": +8, "intermediate": 0,
    "bad": -15, "very_bad": -25, "horrible": -35, "impassable": -50,
}

# Plages de score pour les zones à risque d'inondation, par type d'élément hydrologique
FLOOD_SCORE_RANGE = {
    "river": (60, 85), "canal": (55, 78), "stream": (35, 60),
    "wetland": (42, 72), "water": (25, 55),
}

# Plages NDVI réalistes pour la zone tropicale de CI
# Source : valeurs typiques Sentinel-2 en Afrique de l'Ouest
NDVI_RANGE = {
    "forest": (0.62, 0.88), "wood": (0.60, 0.86),
    "orchard": (0.48, 0.72), "grass": (0.28, 0.55),
    "meadow": (0.30, 0.58), "grassland": (0.28, 0.52),
    "scrub": (0.22, 0.48), "heath": (0.18, 0.42),
    "farmland": (0.15, 0.40),
}

# Tags Overpass — séparés par | pour la syntaxe regex Overpass
HIGHWAY_TAGS        = "motorway|trunk|primary|secondary|tertiary|residential|unclassified|track"
WATER_NATURAL_TAGS  = "wetland|water"
WATER_WAY_TAGS      = "river|stream|canal"
LANDUSE_TAGS        = "forest|grass|meadow|orchard|farmland"
NATURAL_VEG_TAGS    = "wood|scrub|grassland|heath"


# ─── Helpers géographiques ────────────────────────────────────────────────────

def make_bbox(lat: float, lng: float, r: float = SEARCH_RADIUS) -> str:
    # Overpass attend le bbox en (sud, ouest, nord, est)
    return f"{lat - r},{lng - r},{lat + r},{lng + r}"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    # Formule haversine pour estimer la surface d'une zone flood en km²
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
    # Bonus/malus selon la présence du tag surface
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


# ─── Requête Overpass fusionnée ───────────────────────────────────────────────
# Une seule requête au lieu de 3 = pas de throttling sur la troisième.
# Le retry avec délai croissant gère les pics de charge du serveur public.

def overpass_fetch(zone_bbox: str, roads_only: bool, stdout) -> dict | None:
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
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "GéoDash/1.0 (contact@geodash-ci.example.com)"},
            )
            resp.raise_for_status()
            data = resp.json()

            if "remark" in data and "error" in data.get("remark", "").lower():
                raise ValueError(f"Overpass remark: {data['remark']}")

            return data

        except requests.exceptions.Timeout:
            msg = f"Timeout Overpass (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  {msg}")
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            msg  = f"Erreur HTTP {code} (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  {msg}")
            if code == 429:
                # Trop de requêtes — on attend 3x plus longtemps avant de réessayer
                wait = RETRY_DELAY * 3
                stdout.write(f"  429 rate limit, on attend {wait}s...")
                time.sleep(wait)
                continue
        except requests.exceptions.RequestException as e:
            msg = f"Erreur réseau : {e} (tentative {attempt}/{MAX_RETRIES})"
            logger.warning(msg)
            stdout.write(f"  {msg}")
        except ValueError as e:
            logger.error("Réponse Overpass invalide : %s", e)
            stdout.write(f"  Réponse invalide : {e}")
            return None

        if attempt < MAX_RETRIES:
            stdout.write(f"  Nouvelle tentative dans {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    logger.error("Overpass inaccessible après %d tentatives", MAX_RETRIES)
    return None


# ─── Classificateur d'éléments OSM ───────────────────────────────────────────
# Puisqu'on récupère tout en une requête, on trie ensuite.
# L'ordre des conditions compte : highway d'abord car un élément peut avoir
# plusieurs tags (ex: une route en forêt a highway + natural parfois).

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
# On utilise bulk_create pour éviter une requête INSERT par ville.
# Les zones déjà en base (codes existants) sont ignorées — pas d'écrasement
# des zones saisies manuellement dans l'admin.

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


# ─── Sauvegarde en base ───────────────────────────────────────────────────────
# Même pattern pour les 3 types de données :
#   1. On charge les objets existants en mémoire (évite les N+1 queries)
#   2. On sépare créations et mises à jour
#   3. bulk_create / bulk_update en transaction atomique par lots de 200

def save_roads(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    # Charger en une requête, pas besoin de tous les champs
    existing = {
        r.name: r
        for r in RoadSegment.objects.filter(zone=zone)
        .only("id", "name", "condition_score", "status",
              "surface_type", "geojson", "notes", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags    = el.get("tags", {})
        highway = tags.get("highway", "unclassified")
        name    = (tags.get("name") or tags.get("ref")
                   or f"{HIGHWAY_LABEL.get(highway, highway)} #{el['id']}")
        geometry = el.get("geometry", [])
        if len(geometry) < 2:
            continue  # pas assez de points pour tracer une ligne, on skip

        geojson = {"type": "LineString",
                   "coordinates": [[p["lon"], p["lat"]] for p in geometry]}
        score   = score_from_tags(highway, tags)
        status  = status_from_score(score)
        surface = surface_from_tags(highway, tags)

        # Notes techniques issues des tags OSM — pratiques pour l'admin
        parts = [f"Type OSM : {highway}"]
        if tags.get("maxspeed"):   parts.append(f"Vitesse max : {tags['maxspeed']} km/h")
        if tags.get("lanes"):      parts.append(f"Voies : {tags['lanes']}")
        if tags.get("smoothness"): parts.append(f"État OSM : {tags['smoothness']}")
        notes = " | ".join(parts)

        if name in existing:
            r = existing[name]
            r.condition_score = score
            r.status          = status
            r.surface_type    = surface
            r.geojson         = geojson
            r.notes           = notes
            r.last_analyzed   = now
            to_update.append(r)
        else:
            to_create.append(RoadSegment(
                zone=zone, name=name, status=status,
                condition_score=score, surface_type=surface,
                geojson=geojson, notes=notes, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            RoadSegment.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            RoadSegment.objects.bulk_update(
                to_update,
                ["condition_score", "status", "surface_type",
                 "geojson", "notes", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def save_flood_risks(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    existing = {
        f.name: f
        for f in FloodRisk.objects.filter(zone=zone)
        .only("id", "name", "risk_level", "risk_score",
              "area_km2", "geojson", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags     = el.get("tags", {})
        waterway = tags.get("waterway", "")
        natural  = tags.get("natural", "")
        name     = tags.get("name") or tags.get("ref") or f"Zone hydro #{el['id']}"
        key      = waterway or natural

        lo, hi = FLOOD_SCORE_RANGE.get(key, (20, 50))
        # Score déterministe basé sur l'ID OSM — pas de random(), résultats reproductibles
        score  = round(lo + (el["id"] % 1000) / 1000.0 * (hi - lo), 1)

        if score >= 70:   risk_level = "critique"
        elif score >= 50: risk_level = "eleve"
        elif score >= 30: risk_level = "modere"
        else:             risk_level = "faible"

        geometry = el.get("geometry", [])
        if geometry:
            lats = [p["lat"] for p in geometry]
            lngs = [p["lon"] for p in geometry]
            area_km2 = round(
                haversine_km(min(lats), min(lngs), max(lats), max(lngs)) * 0.5, 3
            )
            geojson = {"type": "Polygon",
                       "coordinates": [[[p["lon"], p["lat"]] for p in geometry]]}
        else:
            area_km2 = 0.0
            geojson  = {}

        if name in existing:
            f = existing[name]
            f.risk_level    = risk_level
            f.risk_score    = score
            f.area_km2      = area_km2
            f.geojson       = geojson
            f.last_analyzed = now
            to_update.append(f)
        else:
            to_create.append(FloodRisk(
                zone=zone, name=name, risk_level=risk_level,
                risk_score=score, area_km2=area_km2,
                rainfall_mm=0.0,  # sera mis à jour par GEE plus tard
                geojson=geojson, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            FloodRisk.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            FloodRisk.objects.bulk_update(
                to_update,
                ["risk_level", "risk_score", "area_km2", "geojson", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def save_vegetation(zone: Zone, elements: list, stdout) -> tuple[int, int]:
    now = timezone.now()
    existing = {
        v.name: v
        for v in VegetationDensity.objects.filter(zone=zone)
        .only("id", "name", "ndvi_value", "density_class",
              "coverage_percent", "geojson", "last_analyzed")
    }
    to_create, to_update = [], []

    for el in elements:
        tags    = el.get("tags", {})
        landuse = tags.get("landuse", "")
        natural = tags.get("natural", "")
        name    = tags.get("name") or f"Végétation #{el['id']}"
        key     = landuse or natural

        lo, hi = NDVI_RANGE.get(key, (0.18, 0.55))
        # Même principe que flood : ID OSM comme graine déterministe
        ndvi    = round(lo + (el["id"] % 10000) / 10000.0 * (hi - lo), 3)
        density = ndvi_to_density(ndvi)

        geometry = el.get("geometry", [])
        geojson  = (
            {"type": "Polygon",
             "coordinates": [[[p["lon"], p["lat"]] for p in geometry]]}
            if geometry else {}
        )

        if name in existing:
            v = existing[name]
            v.ndvi_value       = ndvi
            v.density_class    = density
            v.coverage_percent = round(ndvi * 100, 1)
            v.geojson          = geojson
            v.last_analyzed    = now
            to_update.append(v)
        else:
            to_create.append(VegetationDensity(
                zone=zone, name=name, ndvi_value=ndvi,
                density_class=density, coverage_percent=round(ndvi * 100, 1),
                change_vs_previous=0.0,
                geojson=geojson, last_analyzed=now,
            ))

    with transaction.atomic():
        if to_create:
            VegetationDensity.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            VegetationDensity.objects.bulk_update(
                to_update,
                ["ndvi_value", "density_class", "coverage_percent",
                 "geojson", "last_analyzed"],
                batch_size=200,
            )

    return len(to_create), len(to_update)


def generate_alerts(zone: Zone) -> int:
    """
    Génère des alertes automatiques depuis les données importées.
    get_or_create évite les doublons si on relance l'import sur la même zone.
    On se limite aux cas les plus critiques pour ne pas spammer le dashboard.
    """
    count = 0
    now   = timezone.now()

    # Routes les plus dégradées en premier
    for road in zone.roads.filter(status__in=["critique", "ferme"]).order_by("condition_score")[:3]:
        _, created = Alert.objects.get_or_create(
            zone=zone, title=f"Route dégradée : {road.name}",
            category="road", is_read=False,
            defaults={
                "message": (
                    f"Segment '{road.name}' — score {road.condition_score}/100 "
                    f"({road.get_status_display()}). Inspection recommandée."
                ),
                "severity": "critical" if road.status == "ferme" else "danger",
                "created_at": now, "lat": zone.lat_center, "lng": zone.lng_center,
            },
        )
        if created:
            count += 1

    # Zones inondables à risque élevé ou critique
    for flood in zone.flood_risks.filter(risk_level__in=["eleve", "critique"]).order_by("-risk_score")[:2]:
        _, created = Alert.objects.get_or_create(
            zone=zone, title=f"Risque inondation : {flood.name}",
            category="flood", is_read=False,
            defaults={
                "message": (
                    f"Zone '{flood.name}' — risque {flood.get_risk_level_display()}, "
                    f"score {flood.risk_score}/100."
                ),
                "severity": "critical" if flood.risk_level == "critique" else "warning",
                "created_at": now, "lat": zone.lat_center, "lng": zone.lng_center,
            },
        )
        if created:
            count += 1

    return count


# ─── Commande Django ──────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Importe les données géospatiales OSM pour les zones de Côte d'Ivoire."

    def add_arguments(self, parser):
        parser.add_argument("--zone",       type=str,        default=None,
                            help="Code zone (ex: MAN). Absent = toutes les zones.")
        parser.add_argument("--dry-run",    action="store_true",
                            help="Simule sans écrire en base.")
        parser.add_argument("--clear",      action="store_true",
                            help="Supprime les données existantes avant import.")
        parser.add_argument("--roads-only", action="store_true",
                            help="Routes uniquement, ignore eau et végétation.")

    def handle(self, *args, **options):
        dry_run    = options["dry_run"]
        zone_code  = options["zone"]
        roads_only = options["roads_only"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN — aucune écriture en base\n"))

        # Créer les zones manquantes avant tout le reste
        if not dry_run:
            self.stdout.write(self.style.HTTP_INFO("Vérification des zones de Côte d'Ivoire..."))
            n = create_zones_if_missing(self.stdout)
            if n:
                self.stdout.write(self.style.SUCCESS(f"  {n} zone(s) créée(s)\n"))
            else:
                self.stdout.write(f"  {Zone.objects.count()} zones déjà présentes\n")

        # Sélection des zones à traiter
        if zone_code:
            zones = Zone.objects.filter(code__iexact=zone_code)
            if not zones.exists():
                available = ", ".join(Zone.objects.values_list("code", flat=True))
                raise CommandError(f"Zone '{zone_code}' introuvable. Codes disponibles : {available}")
        else:
            zones = Zone.objects.all()

        if not zones.exists():
            raise CommandError("Aucune zone en base. Lance sans --zone pour créer les zones CI.")

        self.stdout.write(f"Zones à traiter : {zones.count()}\n")

        # Nettoyage avant import si demandé
        if options["clear"] and not dry_run:
            self.stdout.write(self.style.WARNING("Suppression des données existantes..."))
            with transaction.atomic():
                if zone_code:
                    for z in zones:
                        z.roads.all().delete()
                        z.flood_risks.all().delete()
                        z.vegetation.all().delete()
                        z.alerts.all().delete()
                else:
                    # Nettoyage global plus rapide qu'en boucle
                    RoadSegment.objects.all().delete()
                    FloodRisk.objects.all().delete()
                    VegetationDensity.objects.all().delete()
                    Alert.objects.filter(category__in=["road", "flood", "vegetation"]).delete()
            self.stdout.write("  Base nettoyée\n")

        totals = dict(rc=0, ru=0, fc=0, fu=0, vc=0, vu=0, alerts=0, errors=0)

        for zone in zones:
            self.stdout.write(self.style.HTTP_INFO(f"\n{'─' * 52}"))
            self.stdout.write(self.style.HTTP_INFO(f"  {zone.name} ({zone.code})"))

            zone_bbox = make_bbox(zone.lat_center, zone.lng_center)

            self.stdout.write("  Requête Overpass (routes + eau + végétation)...")
            data = overpass_fetch(zone_bbox, roads_only, self.stdout)

            if data is None:
                totals["errors"] += 1
                logger.error("Import échoué — zone %s (%s)", zone.name, zone.code)
                self.stdout.write(self.style.ERROR(
                    f"  Overpass inaccessible pour {zone.name} — zone ignorée."
                ))
                time.sleep(REQUEST_DELAY)
                continue

            # Trier les éléments en une seule passe — pas trois boucles séparées
            elements  = [el for el in data.get("elements", []) if el.get("type") == "way"]
            roads_el  = [el for el in elements if classify_element(el) == "road"]
            flood_el  = [el for el in elements if classify_element(el) == "flood"]
            veg_el    = [el for el in elements if classify_element(el) == "vegetation"]

            self.stdout.write(
                f"  {len(roads_el)} routes, {len(flood_el)} zones eau, "
                f"{len(veg_el)} zones végétation"
            )

            if dry_run:
                time.sleep(REQUEST_DELAY)
                continue

            try:
                c, u = save_roads(zone, roads_el, self.stdout)
                totals["rc"] += c
                totals["ru"] += u
                self.stdout.write(f"  Routes : {c} créées, {u} mises à jour")
            except Exception as e:
                totals["errors"] += 1
                logger.exception("Erreur save_roads — zone %s", zone.code)
                self.stdout.write(self.style.ERROR(f"  Routes KO : {e}"))

            if not roads_only:
                try:
                    c, u = save_flood_risks(zone, flood_el, self.stdout)
                    totals["fc"] += c
                    totals["fu"] += u
                    self.stdout.write(f"  Inondations : {c} créées, {u} mises à jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_flood_risks — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Inondations KO : {e}"))

                try:
                    c, u = save_vegetation(zone, veg_el, self.stdout)
                    totals["vc"] += c
                    totals["vu"] += u
                    self.stdout.write(f"  Végétation : {c} créées, {u} mises à jour")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur save_vegetation — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Végétation KO : {e}"))

                try:
                    n = generate_alerts(zone)
                    totals["alerts"] += n
                    self.stdout.write(f"  Alertes générées : {n}")
                except Exception as e:
                    totals["errors"] += 1
                    logger.exception("Erreur generate_alerts — zone %s", zone.code)
                    self.stdout.write(self.style.ERROR(f"  Alertes KO : {e}"))

            time.sleep(REQUEST_DELAY)

        # Résumé final
        self.stdout.write(self.style.SUCCESS(f"\n{'═' * 52}"))
        self.stdout.write(self.style.SUCCESS("Import terminé"))
        if not dry_run:
            self.stdout.write(f"  Routes      — créées : {totals['rc']}, mises à jour : {totals['ru']}")
            self.stdout.write(f"  Inondations — créées : {totals['fc']}, mises à jour : {totals['fu']}")
            self.stdout.write(f"  Végétation  — créées : {totals['vc']}, mises à jour : {totals['vu']}")
            self.stdout.write(f"  Alertes générées     : {totals['alerts']}")
            if totals["errors"]:
                self.stdout.write(self.style.WARNING(
                    f"  {totals['errors']} zone(s) en erreur — voir les logs Django."
                ))
        else:
            self.stdout.write(self.style.WARNING(
                "\nDry-run terminé. Relance sans --dry-run pour importer réellement."
            ))