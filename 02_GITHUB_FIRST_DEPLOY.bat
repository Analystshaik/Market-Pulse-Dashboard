@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

call 01_PREPARE_CLOUD.bat
if errorlevel 1 exit /b 1

where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: Git is not installed or not on PATH.
    exit /b 1
)

git lfs version >nul 2>nul
if errorlevel 1 (
    echo ERROR: Git LFS is not installed.
    echo Install Git LFS, then run this script again.
    exit /b 1
)

git lfs install
if errorlevel 1 exit /b 1

if not exist ".git" (
    git init
    if errorlevel 1 exit /b 1
)

git branch -M main

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    echo.
    echo Create an EMPTY GitHub repository first:
    echo   - Do not add README
    echo   - Do not add .gitignore
    echo   - Do not add a license
    echo.
    set /p REPO_URL=Paste GitHub repository HTTPS URL: 
    if "!REPO_URL!"=="" (
        echo ERROR: Repository URL is required.
        exit /b 1
    )
    git remote add origin "!REPO_URL!"
    if errorlevel 1 exit /b 1
) else (
    echo Existing origin:
    git remote get-url origin
)

git add .
if errorlevel 1 exit /b 1

git diff --cached --quiet
if not errorlevel 1 (
    echo No changes to commit.
) else (
    git commit -m "Initial TLT TBT Streamlit Cloud deployment"
    if errorlevel 1 exit /b 1
)

echo.
echo Pushing main branch to GitHub...
git push -u origin main
if errorlevel 1 (
    echo.
    echo PUSH FAILED.
    echo The easiest first deployment is to use an EMPTY GitHub repository.
    exit /b 1
)

echo.
echo ========================================================================================
echo GITHUB DEPLOYMENT COMPLETE
echo ========================================================================================
echo See DEPLOYMENT_GUIDE.md for the Streamlit Cloud steps.
echo.
endlocal
