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

### Basic Analysis
- Click **Select Project Folder** → pick your project root (optional but helpful).
- Click **Select Log Files** → pick individual `.log` / `.txt` files.
- Click **Analyze** → it will parse error lines, build a short context, call Llama 3 locally, and show the result.
- A **receipt** is written under `receipts/` (append‑only JSONL).

### Pattern Agent (NEW)
The app now includes an intelligent Pattern Agent that provides structured analysis with 4 fixed sections:

**Mode A (Run-once):** Automatically runs after each Analyze operation
- Provides: PATTERNS, ROOT CAUSES, HIGH-RISK TRANSACTIONS, NEXT ACTIONS
- Uses the same log files selected for analysis
- Calls local Ollama model with optimized parameters

**Mode B (Continuous):** Background monitoring
- Click **Start Pattern Agent** → monitors selected log files for changes
- Automatically re-runs analysis when files are modified (20-second intervals)
- Click **Stop Pattern Agent** → stops background monitoring
- Status indicator shows "Running" or "Stopped"

**Features:**
- **Resource caps:** 5MB total, 1MB per file, 8000 char snippets
- **Smart grouping:** Groups logs by transaction ID with fallback patterns
- **Keyword counting:** Tracks error, warn, timeout, fail, exception, perf, latency, search, cart, checkout, payment
- **Deterministic output:** Always produces the same 4-section structure
- **Receipts:** Dedicated `analysis_type: "agent_patterns"` receipts with timing metadata
- **Error resilience:** Graceful fallbacks when model unavailable or files unreadable

## 3) Notes
- Works offline (except the local call to Ollama).
- If Ollama is not running or the model is missing, you'll still get a friendly fallback analysis.
- Data is kept on your laptop. Do not store secrets in logs.

## 4) Troubleshooting

### General Issues
- `'ollama' is not recognized` → Reopen PowerShell after install, or add Ollama to PATH, or reboot once.
- Port in use → change `OLLAMA_URL` in `.env` or the app settings.
- Empty analysis → check that your logs folder has recent `.log` or `.txt` files.

### Pattern Agent Issues
- **No Pattern Agent output** → Ensure log files are selected and Ollama server is running
- **Pattern Agent shows "model unavailable"** → Check Ollama server status and model availability
- **Continuous mode not detecting changes** → Verify log files exist and are being modified
- **Missing receipts** → Check `receipts/` folder for `analysis_type: "agent_patterns"` entries
- **High memory usage** → Pattern Agent respects 5MB total cap; large logs are automatically truncated

### Development Testing
- **Headless check:** `python src/analyzer.py` (reads from `samples/` folder if present)
- **Verify receipts:** Open `receipts/*.jsonl` and look for agent_patterns entries
- **Test continuous mode:** Start agent, modify a log file, wait up to 20 seconds for new analysis

Enjoy!
