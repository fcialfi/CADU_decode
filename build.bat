@echo off
rem Build CADU-Decoder.exe (the window of cadu_gui.py) with PyInstaller.
rem
rem Double-click this file (or run it from a command prompt) in the project
rem folder. It creates the .venv the first time and installs numpy and
rem PyInstaller into it (later runs only add what is missing), deletes the
rem old build and dist folders and builds dist\CADU-Decoder.exe: a single
rem file, to copy anywhere (Python not needed on the target PC).
rem
rem The licence file (license.key) is per customer: send it with the exe, to
rem put in the same folder (see README, Licenza a scadenza dell'eseguibile).

setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe

if not exist %PY% (
    echo === Creating the virtual environment .venv
    python -m venv .venv || goto :error
)

echo === Installing the requirements into .venv
%PY% -m pip install --quiet --upgrade pip || goto :error
%PY% -m pip install --quiet -r requirements.txt pyinstaller || goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dist (
    echo.
    echo The old dist folder could not be deleted: CADU-Decoder.exe is probably open.
    echo Close it and run build.bat again.
    goto :error
)
echo === Building CADU-Decoder.exe
%PY% -m PyInstaller --noconfirm cadu_gui.spec || goto :error

echo.
echo Done: dist\CADU-Decoder.exe
if exist assets\license_public_key.pem (
    echo Licence check on: send the customer's license.key with the exe.
) else (
    echo No assets\license_public_key.pem: this exe has no licence check.
)
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
