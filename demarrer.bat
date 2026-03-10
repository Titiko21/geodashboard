@echo off
echo.
echo  =====================================================
echo   GéoDash — Tableau de Bord Géospatial
echo  =====================================================
echo.

:: Vérifier Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python n'est pas installé ou introuvable dans le PATH.
    echo  Téléchargez Python sur https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Installation de Django...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERREUR] Impossible d'installer Django.
    pause
    exit /b 1
)
echo       OK

echo [2/4] Création de la base de données...
python manage.py migrate --run-syncdb
if errorlevel 1 (
    echo [ERREUR] Échec de la migration.
    pause
    exit /b 1
)
echo       OK

echo [3/4] Chargement des données de démonstration...
python manage.py populate_sample_data
echo       OK

echo [4/4] Démarrage du serveur...
echo.
echo  ✓ Tableau de bord disponible sur : http://127.0.0.1:8000/
echo  ✓ Administration Django         : http://127.0.0.1:8000/admin/
echo.
echo  [Appuyez sur CTRL+C pour arrêter le serveur]
echo.
python manage.py runserver
pause
