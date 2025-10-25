@echo off
echo ========================================
echo AI Log Helper - Starting Ollama Server
echo ========================================
echo.

echo Starting Ollama server for TinyLlama...
echo.
echo IMPORTANT: Keep this window open while using AI Log Helper!
echo The server must stay running for the application to work.
echo.
echo Press Ctrl+C to stop the server when you're done.
echo.

ollama serve

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to start Ollama server!
    echo.
    echo Make sure:
    echo 1. Ollama is installed
    echo 2. TinyLlama model is downloaded (run setup_tinyllama.bat first)
    echo.
    pause
    exit /b 1
)
