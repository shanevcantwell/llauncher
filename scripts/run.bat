@echo off
REM llauncher - Windows runner script
REM Usage: run.bat [mcp|ui|discover|install]

SET SCRIPT_DIR=%~dp0
SET PROJECT_DIR=%SCRIPT_DIR..%

REM Check if virtual environment exists
IF NOT EXIST "%PROJECT_DIR%\.venv" (
    echo [INFO] Virtual environment not found. Creating one...
    cd /d "%PROJECT_DIR%"
    python -m venv .venv
    echo [OK] Virtual environment created
)

REM Activate virtual environment
CALL "%PROJECT_DIR%\.venv\Scripts\activate.bat"

REM Parse command
IF "%~1"=="" GOTO :help
IF "%~1"=="install" GOTO :install
IF "%~1"=="mcp" GOTO :mcp
IF "%~1"=="ui" GOTO :ui
IF "%~1"=="discover" GOTO :discover

GOTO :help

:install
    echo [INFO] Installing llauncher and dependencies...
    pip install -e ".[ui]" >nul 2>&1
    echo [OK] Installation complete
    echo.
    echo Commands available:
    echo   run.bat mcp       - Start MCP server
    echo   run.bat ui        - Start Streamlit UI
    echo   run.bat discover  - List discovered models
    GOTO :end

:mcp
    echo [INFO] Starting MCP server...
    python -m llauncher.mcp.server
    GOTO :end

:ui
    echo [INFO] Starting Streamlit UI...
    streamlit run "%PROJECT_DIR%\llauncher\ui\app.py"
    GOTO :end

:discover
    echo [INFO] Discovering launch scripts...
    python -m llauncher discover
    GOTO :end

:help
    echo llauncher - MCP-first launcher for llama.cpp servers
    echo.
    echo Usage: run.bat [command]
    echo.
    echo Commands:
    echo   install    Install llauncher and dependencies
    echo   mcp        Start MCP server (for LLM clients)
    echo   ui         Start Streamlit UI (dashboard)
    echo   discover   List discovered launch scripts
    echo.
    echo First time setup:
    echo   run.bat install
    GOTO :end

:end
REM Keep window open if run double-clicked
IF "%COMSPEC%"=="" PAUSE
