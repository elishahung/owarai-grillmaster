@echo off
REM Owarai GrillMaster launcher: runs the repo's installed `grill` entry point in the
REM caller's directory, so relative paths (manifests, --out, project dirs) mean what the
REM caller typed. GRILL_HOME points config discovery at the repo root, where grill.toml,
REM .env and projects/ live, when no grill.toml sits above the caller's directory.
setlocal
set "GRILL_HOME=%~dp0.."
"%~dp0..\.venv\Scripts\grill.exe" %*
exit /b %ERRORLEVEL%
