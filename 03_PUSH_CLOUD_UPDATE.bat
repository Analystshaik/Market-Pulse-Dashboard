@echo off
setlocal
cd /d "%~dp0"

call 01_PREPARE_CLOUD.bat
if errorlevel 1 exit /b 1

if not exist ".git" (
    echo ERROR: This Cloud directory is not a Git repository.
    echo Run 02_GITHUB_FIRST_DEPLOY.bat first.
    exit /b 1
)
REM MV_RESEARCH_DEPLOY_GUARD_V1 -- Fail closed if research pages/grids/data are missing.
python VERIFY_MARKETVECTOR_RESEARCH_DEPLOY.py --root ".." --strict-data
if errorlevel 1 (
    echo ERROR: MarketVector Research deployment check failed. Nothing pushed.
    exit /b 1
)


git lfs install >nul
git add .
if errorlevel 1 exit /b 1

git diff --cached --quiet
if not errorlevel 1 (
    echo.
    echo No Cloud changes to push.
    exit /b 0
)

git commit -m "Refresh cloud dashboards and data"
if errorlevel 1 exit /b 1

git push origin main
if errorlevel 1 exit /b 1

echo.
echo Cloud update pushed successfully.
echo Streamlit Community Cloud will detect the GitHub change automatically.
echo.
endlocal
