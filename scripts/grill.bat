@echo off
REM Owarai Grillmaster launcher script
REM Activates the virtual environment and runs main.py with all arguments

REM Change to project root directory (one level up from scripts/)
cd /d "%~dp0.."

REM Run main.py with the virtual environment Python
".venv\Scripts\python.exe" "main.py" %*
