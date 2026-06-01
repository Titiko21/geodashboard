@echo off
echo.
echo  =====================================================
echo   GeoDash — Demarrage Docker dev local
echo  =====================================================
echo.

:: Verifier Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Docker n'est pas disponible. Installer Docker Desktop d'abord.
    echo  https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

:: Verifier que .env existe
if not exist .env (
    echo [ATTENTION] Fichier .env manquant.
    echo  Copier .env.example en .env et renseigner les variables avant de relancer.
    pause
    exit /b 1
)

echo [1/2] Demarrage des conteneurs (db + web)...
docker compose up -d
if errorlevel 1 (
    echo [ERREUR] docker compose up a echoue. Voir les logs.
    pause
    exit /b 1
)
echo       OK

echo [2/2] Attente du health check web (max 60s)...
set /a tries=0
:wait_loop
set /a tries+=1
curl -s -o nul -w "%%{http_code}" http://localhost:8000/health/ 2>nul | findstr "200" >nul
if not errorlevel 1 goto ready
if %tries% geq 30 (
    echo [ATTENTION] Service web pas encore pret apres 60s.
    echo  Verifier : docker compose logs web
    goto end
)
timeout /t 2 /nobreak >nul
goto wait_loop

:ready
echo       OK

echo.
echo  =====================================================
echo   Dashboard       : http://localhost:8000/
echo   Admin Django    : http://localhost:8000/admin/
echo   Health check    : http://localhost:8000/health/
echo  =====================================================
echo.
echo  Logs : docker compose logs -f web
echo  Stop : docker compose down
echo.

:end
pause
