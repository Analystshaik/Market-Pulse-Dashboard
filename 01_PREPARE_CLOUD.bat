@echo off
setlocal
cd /d "%~dp0"

echo ========================================================================================
echo PREPARE TLT/TBT STREAMLIT CLOUD DIRECTORY
echo ========================================================================================
echo.

if exist "..\export_actionable_cloud_db.py" (
    echo Refreshing compact cloud database from the current local snapshot...
    pushd ".."
    python export_actionable_cloud_db.py
    if errorlevel 1 (
        popd
        echo.
        echo ERROR: export_actionable_cloud_db.py failed.
        exit /b 1
    )
    popd
) else (
    echo export_actionable_cloud_db.py was not found in the parent project directory.
    echo Using the existing ..\data\actionable_cloud.sqlite instead.
)

if not exist "..\data\actionable_cloud.sqlite" (
    echo.
    echo ERROR: ..\data\actionable_cloud.sqlite was not found.
    echo Run the EOD pipeline / cloud export first.
    exit /b 1
)

if not exist "data" mkdir "data"

copy /Y "..\data\actionable_cloud.sqlite" "data\actionable_cloud.sqlite" >nul
if errorlevel 1 (
    echo ERROR: Could not copy actionable_cloud.sqlite.
    exit /b 1
)

echo Copied current cloud database.
echo.

python validate_cloud_package.py
if errorlevel 1 (
    echo.
    echo CLOUD PACKAGE VALIDATION FAILED.
    exit /b 1
)

echo.
echo CLOUD DIRECTORY IS READY FOR GITHUB.
echo.
endlocal
