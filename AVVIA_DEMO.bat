@echo off
cd /d "%~dp0"

echo ===============================================
echo   LISTINO FARMACI - WAVE 2 DEMO LOCALE
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creo ambiente Python locale...
    py -m venv .venv 2>nul
    if errorlevel 1 python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Aggiorno le dipendenze necessarie...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERRORE durante l'installazione delle dipendenze.
    pause
    exit /b 1
)

echo.
echo Avvio l'applicazione...
python -m streamlit run app.py
pause
