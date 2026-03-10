"""
constants.py — Constantes partagées du dashboard
==================================================
Centralise les couleurs, choix et seuils utilisés
dans les vues, templates et le JavaScript.
"""

# ── Couleurs par statut / niveau ─────────────────────────
ROAD_COLORS = {
    'bon':      '#22c55e',
    'degrade':  '#f97316',
    'critique': '#ef4444',
    'ferme':    '#6b7280',
}

FLOOD_COLORS = {
    'faible':   '#22d3ee',
    'modere':   '#3b82f6',
    'eleve':    '#f97316',
    'critique': '#dc2626',
}

VEG_COLORS = {
    'sparse':     '#d9f99d',
    'moderate':   '#4ade80',
    'dense':      '#16a34a',
    'very_dense': '#14532d',
}
