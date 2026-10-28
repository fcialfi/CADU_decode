@echo off
rem Build the CADU Decoder (the window of cadu_gui.py) with PyInstaller.
rem
rem Double-click this file (or run it from a command prompt) in the project
rem folder. It:
rem   1. creates the .venv the first time and installs numpy, cryptography and
rem      PyInstaller into it (later runs only add what is missing);
rem   2. deletes the old build and dist folders and builds the folder
rem      dist\CADU-Decoder\ with CADU-Decoder.exe (Python not needed on the
rem      target PC: copy the whole folder);
rem   3. only with "build.bat zip": zips the folder as
rem      dist\CADU-Decoder_<version>.zip (version from version.py), to deliver
rem      it. Without the argument no zip is made.
rem
rem The licence file (license.key) is per customer: send it with the exe, to
rem put in the same folder (see README, Licenza a scadenza dell'eseguibile).

setlocal
cd /d "%~dp0"
set APP=CADU-Decoder
set PY=.venv\Scripts\python.exe

if not exist %PY% (
    echo === Creating the virtual environment .venv
    python -m venv .venv || goto :error
)

echo === Installing the requirements into .venv
%PY% -m pip install --quiet --upgrade pip || goto :error
%PY% -m pip install --quiet -r requirements.txt pyinstaller || goto :error

for /f "delims=" %%v in ('%PY% -c "import version; print(version.APP_VERSION)"') do set VERSION=%%v
if "%VERSION%"=="" goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dist (
    echo.
    echo The old dist folder could not be deleted: some of its files are in use.
    echo Close %APP%.exe ^(also in Task Manager^) and any window open on dist, then run build.bat again.
    goto :error
)
echo === Building %APP% %VERSION%
%PY% -m PyInstaller --noconfirm cadu_gui.spec || goto :error

echo.
echo Done: dist\%APP%\%APP%.exe
if exist assets\license_public_key.pem (
    echo Licence check on: send the customer's license.key with the exe.
) else (
    echo No assets\license_public_key.pem: this exe has no licence check.
)
if /i not "%~1"=="zip" goto :done
echo === Creating dist\%APP%_%VERSION%.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%APP%' -DestinationPath 'dist\%APP%_%VERSION%.zip' -Force" || goto :error
echo Done: dist\%APP%_%VERSION%.zip
echo Remember to add the customer's license.key to the zip.
:done
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
