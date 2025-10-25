"""Log analysis module for AI Log Helper.

This module handles log file analysis, pattern detection, and AI-powered diagnostics.
"""
import os
import glob
import time
from typing import List, Tuple, Dict
from receipts import write_receipt
from ollama_client import ask_llama

ERROR_KEYS = ["error", "exception", "traceback", "failed", "timeout", "fatal", "panic", "stack", "500", " 4xx ", " 5xx ", "warn", "warning", "performance", "resultsCount:0", "high value", "large", "slow", "cart limit", "search", "inventory", "stock", "checkout", "order", "transaction"]

MAX_FILES = 5          # scan up to 5 newest files
MAX_BYTES_TOTAL = 10 * 1024 * 1024  # 10MB cap
CONTEXT_LINES = 4      # lines before/after a hit
MAX_PROMPT_CHARS = 8000

def list_text_files(folder: str) -> List[str]:
    """Get list of text files in folder, sorted by modification time."""
    pats = ["*.log", "*.txt", "*.out"]
    files = []
    for p in pats:
        files.extend(glob.glob(os.path.join(folder, "**", p), recursive=True))
    files_with_time = [(f, os.path.getmtime(f)) for f in files if os.path.isfile(f)]
    files_with_time.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in files_with_time[:MAX_FILES]]

def safe_read(path: str, max_bytes: int) -> str:
    """Safely read file content with error handling."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(max_bytes)
    except (OSError, IOError):
        return ""

def extract_error_windows(text: str) -> Tuple[str, List[str]]:
    """Extract error patterns and create analysis windows."""
    lines = text.splitlines()
    
    # Get the last error (most recent)
    error_lines = []
    for idx, line in enumerate(lines):
        if "ERROR" in line or "error" in line.lower():
            error_lines.append(f"Line {idx + 1}: {line}")
    
    last_error = ""
    if error_lines:
        last_error = error_lines[-1]  # Last error found
    
    # Create structured analysis
    analysis = f"""
LAST ERROR FOUND:
{last_error}

LOG CONTENT FOR ANALYSIS:
{text[:MAX_PROMPT_CHARS]}
"""
    
    patterns = summarize_patterns([ln.lower() for ln in lines])
    return analysis, patterns

def summarize_patterns(lower_lines: List[str]) -> List[str]:
    """Summarize error patterns found in log lines."""
    counts: Dict[str, int] = {}
    for ln in lower_lines:
        for k in ERROR_KEYS:
            if k in ln:
                counts[k.strip()] = counts.get(k.strip(), 0) + 1
    pairs = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [f"{k}:{v}" for k, v in pairs[:6]]

def _detect_project_type(project_folder: str) -> str:
    """Detect project type based on common files."""
    if not project_folder:
        return "unknown"
    
    type_mapping = {
        "package.json": "nodejs",
        "requirements.txt": "python", 
        "pom.xml": "java"
    }
    
    for filename, proj_type in type_mapping.items():
        if os.path.exists(os.path.join(project_folder, filename)):
            return proj_type
    return "unknown"

def _get_project_files(project_folder: str) -> List[str]:
    """Get list of project files."""
    if not project_folder or not os.path.exists(project_folder):
        return []
    
    try:
        return [f for f in os.listdir(project_folder) 
                if os.path.isfile(os.path.join(project_folder, f))]
    except (OSError, IOError):
        return []

def _read_project_context(project_folder: str) -> str:
    """Read key project files for context."""
    if not project_folder or not os.path.exists(project_folder):
        return ""
    
    project_context = ""
    key_files = ['script.js', 'index.html', 'styles.css', 'logger.js']
    
    for filename in key_files:
        filepath = os.path.join(project_folder, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as file_handle:
                    content = file_handle.read(2000)  # First 2000 chars
                    project_context += f"\n\n=== {filename} ===\n{content}\n"
            except (OSError, IOError):
                continue
    return project_context

def build_prompt(project_folder: str, _logs_folder: str, snippets: str, _patterns: List[str]) -> str:
    """Build AI prompt for log analysis."""
    proj_type = _detect_project_type(project_folder)
    project_files = _get_project_files(project_folder)
    project_context = _read_project_context(project_folder)

    prompt = f"""
You are a senior software diagnostics assistant analyzing a restaurant website application.

[Project Context]
- Project type: {proj_type} restaurant website
- Key files: {', '.join(project_files[:10]) if project_files else 'none detected'}
- Project folder: {project_folder or 'not provided'}

[Code Context]
{project_context[:3000]}

[Log Analysis]
{snippets[:MAX_PROMPT_CHARS]}

TASK: Provide a structured analysis with these exact sections:

1. LAST ERROR: Display the exact last error from the log
2. ROOT CAUSE: Detailed analysis of why this error occurred in the project
3. RECTIFICATION STEPS: Specific steps to fix this error

FORMAT - Return exactly this structure:

🚨 LAST ERROR FOUND:
==================================================
[Display the exact last error line from the log file]

🔍 ROOT CAUSE ANALYSIS:
==================================================
[Detailed explanation of why this error occurred in the restaurant website project, including:
- Code issue in the project files
- Business logic problem
- Technical cause of the error]

🛠️ RECTIFICATION STEPS:
==================================================
1) [Specific code change needed in script.js with line numbers]
2) [File modification required]
3) [Configuration change needed]
4) [Testing steps to verify the fix]
5) [Additional preventive measures]

CONSTRAINTS:
- Provide exact file paths and line numbers
- Give specific code changes for the error
- Focus on the restaurant website functionality
- Keep each section detailed but concise
""".strip()
    return prompt

def _process_log_files(files: List[str]) -> Tuple[List[str], List[str]]:
    """Process log files and extract error patterns."""
    bytes_left = MAX_BYTES_TOTAL
    all_snips = []
    patterns_total = []

    for fp in files:
        chunk = safe_read(fp, min(bytes_left, MAX_BYTES_TOTAL))
        if not chunk:
            continue
        snip, patterns = extract_error_windows(chunk)
        if snip:
            all_snips.append(f"### {os.path.basename(fp)}\n{snip}")
        patterns_total.extend(patterns)
        bytes_left -= len(chunk)
        if bytes_left <= 0:
            break

    return all_snips, patterns_total

def _create_receipt(start_time: float, project_folder: str, logs_folder: str, 
                   files: List[str], patterns: List[str], prompt: str, 
                   model: str, answer_json) -> None:
    """Create and write receipt for the analysis."""
    duration_ms = int((time.time() - start_time) * 1000)
    receipt = {
        "run_id": int(start_time * 1_000_000),
        "inputs": {"project_folder": project_folder, "logs_folder": logs_folder, "files_used": files},
        "limits": {"max_bytes_total": MAX_BYTES_TOTAL, "context_lines": CONTEXT_LINES},
        "patterns": patterns[:10],
        "prompt_preview": prompt[:200],
        "model": model,
        "answer": answer_json,
        "metrics": {"duration_ms": duration_ms, "files_scanned": len(files)}
    }
    write_receipt(receipt)

def analyze_folders(project_folder: str, logs_folder: str, ollama_url: str, model: str) -> str:
    """Analyze log files in a folder."""
    start = time.time()
    files = list_text_files(logs_folder)
    if not files:
        return "No logs found. Please choose a folder with .log or .txt files."

    all_snips, patterns_total = _process_log_files(files)
    combined = "\n\n".join(all_snips) if all_snips else "(no obvious error lines found)"
    prompt = build_prompt(project_folder, logs_folder, combined, patterns_total)

    # Ask local Llama
    answer_json = ask_llama(ollama_url, model, prompt)
    _create_receipt(start, project_folder, logs_folder, files, patterns_total, prompt, model, answer_json)

    # Pretty text for GUI
    pretty = [
        "=== AI Log Helper (Local) ===",
        f"Files scanned: {len(files)}",
        f"Top patterns: {', '.join(patterns_total[:6]) if patterns_total else 'none'}",
        "",
    ]
    
    # Parse and format AI response
    analysis_result = _parse_ai_response(answer_json)
    pretty.append("📊 AI ANALYSIS RESULTS:")
    pretty.append("=" * 50)
    pretty.append("")
    pretty.append(analysis_result)

    pretty.append("")
    pretty.append("📄 A detailed receipt was saved to the receipts/ folder.")
    return "\n".join(pretty)

def _process_specific_log_files(log_files: List[str]) -> Tuple[List[str], List[str]]:
    """Process specific log files and extract error patterns."""
    bytes_left = MAX_BYTES_TOTAL
    all_snips = []
    patterns_total = []

    for fp in log_files:
        if not os.path.isfile(fp):
            continue
        chunk = safe_read(fp, min(bytes_left, MAX_BYTES_TOTAL))
        if not chunk:
            continue
        snip, patterns = extract_error_windows(chunk)
        if snip:
            all_snips.append(f"### {os.path.basename(fp)}\n{snip}")
        patterns_total.extend(patterns)
        bytes_left -= len(chunk)
        if bytes_left <= 0:
            break

    return all_snips, patterns_total

def _parse_ai_response(answer_json) -> str:
    """Parse AI response and format it properly."""
    if isinstance(answer_json, dict):
        # Legacy JSON format
        root_cause = answer_json.get("root_cause", "Analysis unavailable")
        why_items = answer_json.get("why", [])
        next_steps = answer_json.get("next_steps", [])
        
        result = f"Root Cause: {root_cause}\n"
        
        if why_items:
            result += "\n📋 DETAILED ANALYSIS:\n"
            result += "-" * 30 + "\n"
            for i, w in enumerate(why_items, 1):
                result += f"{i}. {w}\n"
        
        if next_steps:
            result += "\n🚀 RECOMMENDED ACTIONS:\n"
            result += "-" * 30 + "\n"
            for i, step in enumerate(next_steps, 1):
                result += f"{i}. {step}\n"
        
        return result
    else:
        # Markdown format - return as-is
        return str(answer_json)

def _create_files_receipt(start_time: float, project_folder: str, log_files: List[str], 
                        patterns: List[str], prompt: str, model: str, answer_json) -> None:
    """Create and write receipt for file analysis."""
    duration_ms = int((time.time() - start_time) * 1000)
    receipt = {
        "run_id": int(start_time * 1_000_000),
        "inputs": {"project_folder": project_folder, "log_files": log_files},
        "limits": {"max_bytes_total": MAX_BYTES_TOTAL, "context_lines": CONTEXT_LINES},
        "patterns": patterns[:10],
        "prompt_preview": prompt[:200],
        "model": model,
        "answer": answer_json,
        "metrics": {"duration_ms": duration_ms, "files_scanned": len(log_files)}
    }
    write_receipt(receipt)

def analyze_files(project_folder: str, log_files: list, ollama_url: str, model: str) -> str:
    """Analyze specific log files instead of scanning a folder."""
    start = time.time()
    
    if not log_files:
        return "No log files provided."

    all_snips, patterns_total = _process_specific_log_files(log_files)
    combined = "\n\n".join(all_snips) if all_snips else "(no obvious error lines found)"
    prompt = build_prompt(project_folder, f"{len(log_files)} selected files", combined, patterns_total)

    # Ask local Llama
    answer_json = ask_llama(ollama_url, model, prompt)
    _create_files_receipt(start, project_folder, log_files, patterns_total, prompt, model, answer_json)

    # Pretty text for GUI
    pretty = [
        "=== AI Log Helper (Local) ===",
        f"Files scanned: {len(log_files)}",
        f"Top patterns: {', '.join(patterns_total[:6]) if patterns_total else 'none'}",
        "",
    ]
    
    # Parse and format AI response
    analysis_result = _parse_ai_response(answer_json)
    pretty.append("📊 AI ANALYSIS RESULTS:")
    pretty.append("=" * 50)
    pretty.append("")
    pretty.append(analysis_result)

    pretty.append("")
    pretty.append("📄 A detailed receipt was saved to the receipts/ folder.")
    return "\n".join(pretty)
