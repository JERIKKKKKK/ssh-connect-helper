@echo off
rem Build SSH_Connect.exe. ASCII-only file on purpose: no codepage problems.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

rem tkinterdnd2 (drag and drop from Explorer): first try pip; if pip cannot install it
rem (no internet / proxy), use the copy bundled in the vendor_dnd folder next to this file.
python -m pip install --upgrade tkinterdnd2
python -c "import tkinterdnd2" >nul 2>nul
if errorlevel 1 (
    echo pip could not install tkinterdnd2 - using the bundled copy from vendor_dnd
    set "PYTHONPATH=%~dp0vendor_dnd;%PYTHONPATH%"
)
python -c "import tkinterdnd2, os; d=os.path.join(os.path.dirname(tkinterdnd2.__file__),'tkdnd'); print('tkinterdnd2 OK:', tkinterdnd2.__file__); raise SystemExit(0 if os.path.isdir(d) else 1)"
if errorlevel 1 goto fail_dnd

python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
    --name SSH_Connect ^
    --icon assets\ssh_connect.ico ^
    --add-data "assets\ssh_connect.ico;assets" ^
    --add-data "assets\ssh_connect.png;assets" ^
    --collect-all customtkinter ^
    --collect-all pyte ^
    --collect-all tkinterdnd2 ^
    --hidden-import tkinterdnd2 ^
    --collect-submodules paramiko ^
    --collect-submodules openpyxl ^
    --hidden-import et_xmlfile ^
    --hidden-import darkdetect ^
    ssh_connect.py
if errorlevel 1 goto fail

echo.
echo Done: dist\SSH_Connect.exe
if /i not "%~1"=="nopause" pause
exit /b 0

:fail_dnd
echo.
echo tkinterdnd2 not found: neither pip nor the vendor_dnd folder worked.
echo Make sure the vendor_dnd folder is next to build_exe.bat, or send me the text above.
if /i not "%~1"=="nopause" pause
exit /b 1

:fail
echo.
echo Build failed, see messages above.
if /i not "%~1"=="nopause" pause
exit /b 1
