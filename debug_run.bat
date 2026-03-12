@echo off
echo Starting PCCP Monitor in DEBUG mode...
echo.

cd /d "%~dp0"
python src\main.py

echo.
echo Program ended. Press any key to exit...
pause > nul