"""
Fixtures partagées pour les tests GéoDash.

Notes PostGIS :
  Django 4+ avec le backend `django.contrib.gis.db.backends.postgis` crée
  automatiquement l'extension PostGIS dans la DB de test si l'utilisateur DB
  a les droits CREATE EXTENSION (cas par défaut sur l'image postgis/postgis).
  Aucune fixture n'est donc nécessaire ici pour l'activer.
"""
import pytest
from django.contrib.auth import get_user_model

from dashboard.models import Zone


@pytest.fixture
def zone(db):
    """Une Zone minimale pour les tests qui en ont besoin."""
    return Zone.objects.create(
        name="Test Zone",
        code="TST",
        lat_center=5.35,
        lng_center=-4.00,
        description="Zone de test",
    )


@pytest.fixture
def user(db):
    """Un utilisateur Django pour passer LoginRequiredMiddleware."""
    return get_user_model().objects.create_user(
        username="testuser",
        password="testpass",
        email="test@example.com",
    )


@pytest.fixture
def auth_client(client, user):
    """Client Django Test authentifié (force_login).

    Les tests qui ciblent des endpoints derrière LoginRequiredMiddleware
    doivent utiliser `auth_client` (et non `client`).
    """
    client.force_login(user)
    return client
