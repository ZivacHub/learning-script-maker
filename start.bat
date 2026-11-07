@echo off
rem Learning Script Maker - pokretanje (dvaput klikni). Slusa samo na 127.0.0.1.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Treba "uv" ^(upravitelj Python okruzenja^). Instalacija: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
if "%SM_PORT%"=="" set SM_PORT=8765
netstat -ano | findstr /R /C:":%SM_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo Port %SM_PORT% je zauzet ^(mozda je aplikacija vec pokrenuta^). Probaj: set SM_PORT=8766 pa opet start.bat
  pause
  exit /b 1
)
echo Pokrecem na http://127.0.0.1:%SM_PORT%  ^(zatvori ovaj prozor za gasenje^)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%SM_PORT%"
uv run python -m scriptmaker
pause
