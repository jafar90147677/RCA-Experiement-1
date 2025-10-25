# AI Log Helper (GUI) — Local (Ollama + Llama 3)

A tiny Windows‑friendly app that scans your **project folder** and **logs folder**, then asks a **local** Llama 3 model (via **Ollama**) to suggest a **root cause**, **why**, and **next steps**.

## 0) What you need
- Windows 10/11
- Python 3.11+
- VS Code + Cursor (optional but recommended)
- **Ollama** (local model runner)

### Install Ollama (Windows)
1. Download the Windows installer from the official site and install.
2. Open **PowerShell** and run:
   ```powershell
   ollama --version
   ollama pull llama3
   ollama serve
   ```
   Keep `ollama serve` running. If `ollama` is not recognized, close & reopen PowerShell, or reboot once.

> Default server URL is `http://127.0.0.1:11434`

## 1) Set up this project
```powershell
cd ai-log-helper-gui
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python src\main.py
```

## 2) Use the app
- Click **Select Project Folder** → pick your project root (optional but helpful).
- Click **Select Logs Folder** → pick the folder with `.log` / `.txt` files.
- Click **Analyze** → it will parse error lines, build a short context, call Llama 3 locally, and show the result.
- A **receipt** is written under `receipts/` (append‑only JSONL).

## 3) Notes
- Works offline (except the local call to Ollama).
- If Ollama is not running or the model is missing, you'll still get a friendly fallback analysis.
- Data is kept on your laptop. Do not store secrets in logs.

## 4) Troubleshooting
- `'ollama' is not recognized` → Reopen PowerShell after install, or add Ollama to PATH, or reboot once.
- Port in use → change `OLLAMA_URL` in `.env` or the app settings.
- Empty analysis → check that your logs folder has recent `.log` or `.txt` files.

Enjoy!
