@echo off
rem Builds SSH_Connect.exe and then the installer dist\SSH_Connect_Setup_X.Y.Z.exe (Inno Setup).
rem ASCII-only file on purpose: no codepage problems.
rem   build_installer.bat            - build exe + installer
rem   build_installer.bat skipbuild  - installer only, from the existing dist\SSH_Connect.exe
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if /i "%~1"=="skipbuild" goto have_exe
call build_exe.bat nopause
if errorlevel 1 goto fail
:have_exe
if not exist "dist\SSH_Connect.exe" (
    echo dist\SSH_Connect.exe not found - run build_exe.bat first.
    goto fail
)

for /f "delims=" %%v in ('python get_version.py') do set VER=%%v
if "%VER%"=="" set VER=1.0.0

call :find_iscc
if defined ISCC goto run_iscc
echo Inno Setup 6 not found. Trying to install it with winget...
where winget >nul 2>nul
if not errorlevel 1 winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
call :find_iscc
if defined ISCC goto run_iscc
echo.
echo Could not find Inno Setup (ISCC.exe). Install Inno Setup 6.3 or newer from
echo https://jrsoftware.org/isdl.php and run this file again.
goto fail

:run_iscc
echo Using %ISCC%
echo Version: %VER%
"%ISCC%" /Qp "/DAppVersion=%VER%" installer\installer.iss
if errorlevel 1 goto fail
echo.
echo Done: dist\SSH_Connect_Setup_%VER%.exe
if not defined CI pause
exit /b 0

:find_iscc
set ISCC=
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist %%p set ISCC=%%~p
)
if not defined ISCC for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do set ISCC=%%i
exit /b 0

:fail
echo.
echo Build failed, see messages above.
if not defined CI pause
exit /b 1
