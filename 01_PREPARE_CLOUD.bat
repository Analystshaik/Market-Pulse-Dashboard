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
    if errorlevel 1 ( popd & echo ERROR: export_actionable_cloud_db.py failed. & exit /b 1 )
    popd
)
if exist "..\export_monthly_tlt_cloud_tables.py" (
    echo Adding Monthly TLT SPY/QQQ/DIA comparison tables...
    pushd ".."
    python export_monthly_tlt_cloud_tables.py
    if errorlevel 1 ( popd & echo ERROR: export_monthly_tlt_cloud_tables.py failed. & exit /b 1 )
    popd
)
if not exist "..\data\actionable_cloud.sqlite" ( echo ERROR: ..\data\actionable_cloud.sqlite was not found. & exit /b 1 )
if not exist "data" mkdir "data"
copy /Y "..\data\actionable_cloud.sqlite" "data\actionable_cloud.sqlite" >nul
if errorlevel 1 ( echo ERROR: Could not copy actionable_cloud.sqlite. & exit /b 1 )
python validate_cloud_package.py
if errorlevel 1 ( echo CLOUD PACKAGE VALIDATION FAILED. & exit /b 1 )
echo.
echo CLOUD DIRECTORY IS READY FOR GITHUB.
endlocal
