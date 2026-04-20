@echo off
REM Auto-Cleanup fuer AI Research Agent
REM - Loescht logs und results aelter als 30 Tage
REM - Loescht RBs mit status error/cancelled/max_iterations aelter als 90 Tage
REM - Schreibt ein Log-File mit Ergebnis

set "HERE=%~dp0"
cd /d "%HERE%"

set "LOGFILE=%HERE%logs\cleanup_%date:~-4%%date:~3,2%%date:~0,2%.log"

call "%HERE%venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [cleanup] venv not found at %HERE%venv > "%LOGFILE%"
    exit /b 1
)

python "%HERE%cleanup.py" ^
    --logs-days 30 ^
    --results-days 30 ^
    --rbs-days 90 ^
    --rbs-status error,cancelled,max_iterations ^
    --apply >> "%LOGFILE%" 2>&1

exit /b %errorlevel%
