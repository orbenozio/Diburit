@echo off
setlocal EnableDelayedExpansion
echo.
echo  ========================================
echo   Diburit Windows Installer
echo  ========================================
echo.

REM ── Python check ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Install Python 3.9+ from https://python.org
    echo  Make sure to check "Add python.exe to PATH" during install.
    echo.
    pause & exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python %PYVER% found.

REM ── tkinter check ─────────────────────────────────────────────────────────
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: tkinter is not available.
    echo  Re-run the Python installer and enable "tcl/tk and IDLE".
    echo.
    pause & exit /b 1
)
echo  tkinter OK.

REM ── Virtual environment ───────────────────────────────────────────────────
if not exist ".venv" (
    echo.
    echo  Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 ( echo  ERROR: venv creation failed. & pause & exit /b 1 )
)
echo  Virtual environment ready.

REM ── Dependencies ──────────────────────────────────────────────────────────
echo.
echo  Installing dependencies (this may take a minute)...
call .venv\Scripts\activate.bat
pip install -r requirements_win.txt --quiet
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed. See above for details.
    pause & exit /b 1
)
echo  Dependencies installed.

REM ── Diburit home directories ──────────────────────────────────────────────
if not exist "%USERPROFILE%\Diburit" (
    mkdir "%USERPROFILE%\Diburit"
    echo  Created %USERPROFILE%\Diburit
)
if not exist "%USERPROFILE%\Diburit\recordings" (
    mkdir "%USERPROFILE%\Diburit\recordings"
)

REM ── GROQ_API_KEY check ────────────────────────────────────────────────────
if not exist "%USERPROFILE%\Diburit\.env" (
    echo.
    echo  ----------------------------------------------------------------
    echo  ACTION REQUIRED: Create the file
    echo    %USERPROFILE%\Diburit\.env
    echo  with this content:
    echo    GROQ_API_KEY=your_key_here
    echo.
    echo  Get a free key at https://console.groq.com
    echo  ----------------------------------------------------------------
)

REM ── Windows Defender / AV warning ────────────────────────────────────────
echo.
echo  ----------------------------------------------------------------
echo  ANTIVIRUS NOTE:
echo  Diburit installs a global keyboard hook to detect your hotkey.
echo  Windows Defender or your antivirus may flag this as suspicious.
echo  If Diburit is quarantined, add an exclusion in:
echo    Windows Security -^> Virus and threat protection -^> Exclusions
echo    Add folder: %CD%
echo  ----------------------------------------------------------------

REM ── Microphone permission reminder ───────────────────────────────────────
echo.
echo  MICROPHONE: Make sure desktop apps can access the mic:
echo    Settings -^> Privacy ^& Security -^> Microphone
echo    Turn ON "Let desktop apps access your microphone"

REM ── Startup shortcut (optional) ───────────────────────────────────────────
echo.
set /p ADD_STARTUP="  Add Diburit to Windows startup? [y/N]: "
if /i "!ADD_STARTUP!"=="y" (
    set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Diburit.lnk
    set PYWIN=%CD%\.venv\Scripts\pythonw.exe
    powershell -NoProfile -Command ^
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('!SHORTCUT!'); ^
         $s.TargetPath='!PYWIN!'; ^
         $s.Arguments='diburit_win.py'; ^
         $s.WorkingDirectory='%CD%'; ^
         $s.WindowStyle=7; ^
         $s.Save()"
    if errorlevel 1 (
        echo  WARNING: Could not create startup shortcut.
    ) else (
        echo  Startup shortcut created: !SHORTCUT!
    )
)

REM ── Done ──────────────────────────────────────────────────────────────────
echo.
echo  ========================================
echo   Installation complete!
echo  ========================================
echo.
echo  To start Diburit:
echo    .venv\Scripts\pythonw.exe diburit_win.py
echo.
echo  (Use pythonw.exe to avoid a console window.)
echo.
pause
