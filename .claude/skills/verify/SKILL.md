---
name: verify
description: Vérifier un changement GéoDash à chaud — lancer un runserver jetable sur la base existante et piloter le dashboard dans le navigateur.
---

# Vérifier GéoDash (worktree ou checkout principal)

L'app tourne normalement en Docker depuis le checkout principal
(`O:\geodashboard\geedashboard` → conteneur `geedashboard-web-1`, port 8000,
bind-mount `/app`). Un worktree n'est PAS servi par ce conteneur : lancer un
second conteneur jetable qui monte le worktree.

## Recette (validée 2026-07-15)

```bash
# 1. Récupérer l'env du conteneur qui tourne (contient secrets — fichier temp, à supprimer après)
docker inspect geedashboard-web-1 --format '{{range .Config.Env}}{{println .}}{{end}}' > "$SCRATCH/web.env"
# 2. Désactiver Keycloak : le client OIDC n'accepte que le port 8000 → "Invalid parameter: redirect_uri" sinon
grep -v '^KEYCLOAK_ENABLED' "$SCRATCH/web.env" > "$SCRATCH/web-noauth.env"; echo "KEYCLOAK_ENABLED=false" >> "$SCRATCH/web-noauth.env"
# 3. Lancer sur :8010, même réseau + même DB (lecture seule : pas de migrate/import)
docker run -d --name gds-verify --entrypoint python \
  --network geedashboard_default -p 8010:8000 \
  --env-file "$SCRATCH/web-noauth.env" \
  -v "<CHEMIN_WORKTREE>:/app" \
  -v "O:\Confidentiel\private-key.json:/app/gee_credentials.json:ro" \
  geedashboard-local:dev manage.py runserver 0.0.0.0:8000
# Prêt quand http://localhost:8010/health/ → 200 (~15 s)
```

Nettoyage : `docker rm -f gds-verify` + supprimer les fichiers env.

## Gotchas

- `--entrypoint python` obligatoire : `entrypoint.sh` du worktree a des fins
  de ligne CRLF (checkout Windows) → `exec /app/entrypoint.sh: no such file`.
- Ne PAS relancer migrate/import contre la DB partagée sauf si la branche
  contient des migrations (elle écrit dans la base du conteneur principal).
- Sélection d'une commune : `/?admin=commune:CIV-COM-<NOM>` (ex.
  `CIV-COM-MARCORY`) — indispensable pour rendre `flood_profile` côté serveur.
- Réglages UI persistés dans `localStorage['gd-settings-v3']` — à seeder/reset
  via JS pour tester les migrations de réglages.
- Dans le pane navigateur, `computer{screenshot}` peut expirer sur cette app
  (tuiles) ; `read_page`, `get_page_text` et `javascript_tool` fonctionnent.
