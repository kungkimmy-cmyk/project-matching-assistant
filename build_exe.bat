@echo off
REM 2025 Project Matching Assistant -- TEST BUILD .exe build script
REM Run this ON WINDOWS, from this project's root folder.

echo Installing dependencies...
pip install --upgrade pip
pip install openpyxl PySide6 pyinstaller reportlab pillow

echo NOTE: reading legacy .xls files also needs LibreOffice installed
echo separately (https://www.libreoffice.org/download). .xlsx works without it.

echo Building the TEST executable...
pyinstaller --noconfirm --windowed --name Project_Matching_Assistant_TEST_BUILD main.py

echo.
echo Done. Find your app at:
echo   dist\Project_Matching_Assistant_TEST_BUILD\Project_Matching_Assistant_TEST_BUILD.exe
echo.
echo Zip the whole "dist\Project_Matching_Assistant_TEST_BUILD" folder to share it.
pause
