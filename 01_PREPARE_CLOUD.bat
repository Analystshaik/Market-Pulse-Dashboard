@echo off
setlocal
cd /d "%~dp0"
echo ========================================================================================
echo PREPARE TLT/TBT STREAMLIT CLOUD DIRECTORY
echo ========================================================================================
echo.
REM MARKETVECTOR_TLT_PENDING_HOLD_SAFETY_V11
REM v1.0 fallback records can be overwritten by downstream trend/reversal enrichment.
REM Back up changed snapshot rows, restore HOLD, and stop export if verification fails.
python ..\FIX_TLT_PENDING_HOLD_STATE.py --root ".." --repair
if errorlevel 1 (
  echo ERROR: TLT research pending safety correction failed. Do not deploy.
  exit /b 1
)

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
python ADD_TREND_TABLE_TO_CLOUD.py
if errorlevel 1 (
    echo ERROR: Could not add daily trend table to compact Cloud DB.
    exit /b 1
)

python STAMP_MARKET_PULSE_ANALYST_DATE.py
if errorlevel 1 (
    echo ERROR: Failed to stamp Market Pulse analyst-data date.
    exit /b 1
)

python ADD_TBT_RESEARCH_TO_CLOUD.py
if errorlevel 1 (
    echo ERROR: TBT research export failed. Do not deploy stale research data.
    exit /b 1
)

python tbt_research_client_context.py
if errorlevel 1 (
    echo ERROR: Research client grid export failed. Cloud publish stopped.
    exit /b 1
)

REM TLT_RESEARCH_VISIBILITY_EXPORT_V1
python tlt_research_export.py
if errorlevel 1 (
  echo ERROR: TLT research export failed. Do not publish cloud.
  exit /b 1
)
python tlt_research_client_context.py
if errorlevel 1 (
  echo ERROR: TLT research context export failed. Do not publish cloud.
  exit /b 1
)

python VERIFY_TLT_RESEARCH_CONTEXT.py
if errorlevel 1 (
  echo ERROR: Research context verification failed. Do not deploy.
  exit /b 1
)

REM MARKETVECTOR_TLT_ADMISSION_VERIFY_V1
python ..\VERIFY_TLT_SNAPSHOT_ADMISSION.py --root ".." --cloud-required
if errorlevel 1 (
  echo ERROR: TLT snapshot admission safety verification failed. Do not deploy.
  exit /b 1
)

python validate_cloud_package.py
if errorlevel 1 ( echo CLOUD PACKAGE VALIDATION FAILED. & exit /b 1 )
echo.
echo CLOUD DIRECTORY IS READY FOR GITHUB.
endlocal
