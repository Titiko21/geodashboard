"""
Tests du health check. Doit toujours répondre 200 + JSON structuré,
sinon les healthchecks Docker passent en unhealthy et le compose plante.
"""
import json

import pytest


pytestmark = pytest.mark.django_db


def test_health_returns_200(client):
    resp = client.get("/health/")
    assert resp.status_code == 200


def test_health_returns_expected_structure(client):
    resp = client.get("/health/")
    data = json.loads(resp.content)

    assert "status" in data
    assert "timestamp" in data
    assert "checks" in data

    # Les 3 checks critiques qu'on garantit
    assert "database" in data["checks"]
    assert "dns_db" in data["checks"]
    assert "data" in data["checks"]


def test_health_database_check_passes_in_test_env(client):
    """Si ce test échoue, la DB de test n'est pas joignable — pytest aurait
    de toute façon déjà planté avant. Sanity check du runtime de test."""
    resp = client.get("/health/")
    data = json.loads(resp.content)
    assert data["checks"]["database"]["status"] == "ok"
