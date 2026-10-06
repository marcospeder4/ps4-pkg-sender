@echo off
REM Genera "PS4 PKG Sender.exe" en la carpeta dist\
REM Requisito: Python 3 instalado y agregado al PATH.

python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --windowed --clean --name "PS4 PKG Sender" ps4_pkg_sender.py

echo.
echo Listo. Tu programa esta en: dist\PS4 PKG Sender.exe
pause
