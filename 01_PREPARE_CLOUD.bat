@echo off
setlocal
cd /d "%~dp0"

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- Pending TLT Research Hold safety
python ..\FIX_TLT_PENDING_HOLD_STATE.py --root ".." --repair
if errorlevel 1 (
    echo ERROR: Pending TLT Research Hold safety failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

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
REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TBT Positive Research export
python ADD_TBT_RESEARCH_TO_CLOUD.py
if errorlevel 1 (
    echo ERROR: TBT Positive Research export failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TBT research grid context
python tbt_research_client_context.py
if errorlevel 1 (
    echo ERROR: TBT research grid context failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TLT Positive Research export
python tlt_research_export.py
if errorlevel 1 (
    echo ERROR: TLT Positive Research export failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TLT research grid context
python tlt_research_client_context.py
if errorlevel 1 (
    echo ERROR: TLT research grid context failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TLT research context verification
python VERIFY_TLT_RESEARCH_CONTEXT.py
if errorlevel 1 (
    echo ERROR: TLT research context verification failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

REM EOD_CLOUD_PREFLIGHT_REPAIR_V1.0.1 -- TLT snapshot admission verification
python ..\VERIFY_TLT_SNAPSHOT_ADMISSION.py --root ".." --cloud-required
if errorlevel 1 (
    echo ERROR: TLT snapshot admission verification failed. CLOUD PUSH BLOCKED.
    exit /b 1
)

python validate_cloud_package.py
if errorlevel 1 ( echo CLOUD PACKAGE VALIDATION FAILED. & exit /b 1 )
echo.
echo CLOUD DIRECTORY IS READY FOR GITHUB.
endlocal
