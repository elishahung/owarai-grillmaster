@echo off
REM Owarai GrillMaster launcher: runs the installed `grill` entry point from the repo root
REM (projects/, .env and config files resolve relative to it).
cd /d "%~dp0.."
".venv\Scripts\grill.exe" %*
