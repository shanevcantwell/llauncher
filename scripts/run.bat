@echo off
REM llauncher - Windows runner script
REM Usage: run.bat [mcp|ui|agent|discover|install]

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."

REM Parse command early to handle install differently
if "%~1"=="" goto :help
if /i "%~1"=="install" goto :install

REM For all other commands, ensure venv exists first
if not exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo Please run: run.bat install
    exit /b 1
)

REM Activate virtual environment by setting PATH
set "PYTHON_EXECUTABLE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "PATH=%PROJECT_DIR%\.venv\Scripts;%PATH%"

REM Now parse and execute command
if /i "%~1"=="mcp" goto :mcp
if /i "%~1"=="ui" goto :ui
if /i "%~1"=="agent" goto :agent
if /i "%~1"=="agent-bg" goto :agent-bg
if /i "%~1"=="stop" goto :stop
if /i "%~1"=="discover" goto :discover

goto :help

:install
    echo [INFO] Installing llauncher and dependencies...

    REM Check if venv exists, create if not
    if not exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
        echo [INFO] Virtual environment not found. Creating one...
        cd /d "%PROJECT_DIR%"
        python -m venv .venv
        if errorlevel 1 (
            echo [ERROR] Failed to create virtual environment
            exit /b 1
        )
        echo [OK] Virtual environment created
        echo.
    )

    REM Upgrade pip and install dependencies
    echo [INFO] Upgrading pip...
    "%PROJECT_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1

    echo [INFO] Installing llauncher with UI dependencies...
    cd /d "%PROJECT_DIR%"
    "%PROJECT_DIR%\.venv\Scripts\python.exe" -m pip install -e ".[ui]"
    if errorlevel 1 (
        echo [ERROR] Installation failed
        exit /b 1
    )

    echo.
    echo [OK] Installation complete
    echo.
    echo Commands available:
    echo   run.bat mcp       - Start MCP server
    echo   run.bat ui        - Start Streamlit UI (auto-starts agent)
    echo   run.bat agent     - Start remote management agent (foreground)
    echo   run.bat stop      - Stop running agent
    echo   run.bat discover  - List discovered models
    goto :end

:mcp
    echo [INFO] Starting MCP server...
    "%PYTHON_EXECUTABLE%" -m llauncher.mcp.server
    goto :end

:ui
    echo [INFO] Starting Streamlit UI...
    "%PYTHON_EXECUTABLE%" -m streamlit run "%PROJECT_DIR%\llauncher\ui\app.py"
    goto :end

:agent
    echo [INFO] Starting remote management agent...
    echo [INFO] Agent will listen on 0.0.0.0:8765
    echo [INFO] Set LAUNCHER_AGENT_PORT and LAUNCHER_AGENT_NODE_NAME to customize
    "%PYTHON_EXECUTABLE%" -m llauncher.agent
    goto :end

:agent-bg
    echo [INFO] Starting remote management agent in background...
    start /B "%PYTHON_EXECUTABLE%" -m llauncher.agent > "%PROJECT_DIR%\agent.log" 2>&1
    echo [OK] Agent started in background
    echo Logs: %PROJECT_DIR%\agent.log
    goto :end

:stop
    echo [INFO] Stopping remote management agent...
    "%PYTHON_EXECUTABLE%" -m llauncher.agent --stop
    goto :end

:discover
    echo [INFO] Discovering launch scripts...
    "%PYTHON_EXECUTABLE%" -m llauncher discover
    goto :end

:help
    echo llauncher - MCP-first launcher for llama.cpp servers
    echo.
    echo Usage: run.bat [command]
    echo.
    echo Commands:
    echo   install    Install llauncher and dependencies
    echo   mcp        Start MCP server (for LLM clients)
    echo   ui         Start Streamlit UI (auto-starts agent)
    echo   agent      Start remote management agent (foreground)
    echo   agent-bg   Start remote management agent (background)
    echo   stop       Stop running agent
    echo   discover   List discovered launch scripts
    echo.
    echo Environment variables for agent:
    echo   LAUNCHER_AGENT_HOST     Host to bind to (default: 0.0.0.0)
    echo   LAUNCHER_AGENT_PORT     Port to listen on (default: 8765)
    echo   LAUNCHER_AGENT_NODE_NAME Friendly name for this node
    echo.
    echo First time setup:
    echo   run.bat install
    goto :end

:end
exit /b 0
