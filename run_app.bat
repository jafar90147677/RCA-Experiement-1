@echo off
echo ========================================
echo AI Log Helper - Starting Application
echo ========================================
echo.

echo Starting AI Log Helper GUI...
echo Make sure Ollama server is running (use start_server.bat)
echo.

python src\main.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to start the application!
    echo.
    echo Make sure:
    echo 1. Python is installed and in PATH
    echo 2. You're in the project directory
    echo 3. Dependencies are installed: pip install -r requirements.txt
    echo 4. Ollama server is running (use start_server.bat)
    echo.
    pause
    exit /b 1
)
