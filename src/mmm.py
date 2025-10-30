"""MMM (Mirror/Mentor/Multiplier) helpers.

Pure, deterministic functions to generate short MMM lines from a last error.
Optionally refines with local Ollama if available; falls back safely.
"""
from typing import Tuple, List, Optional
import os
import re

try:
    # Optional import; caller may not have Ollama running
    from ollama_client import ask_llama  # type: ignore
except Exception:  # pragma: no cover - unavailable in tests
    ask_llama = None  # type: ignore


def _tail_lines(paths: List[str], bytes_cap_total: int = 500_000, bytes_cap_per_file: int = 200_000) -> List[str]:
    lines: List[str] = []
    approx = 0
    # newest first
    files = []
    for p in paths or []:
        try:
            if os.path.isfile(p):
                files.append((p, os.path.getmtime(p)))
        except OSError:
            continue
    files.sort(key=lambda t: t[1], reverse=True)
    for fp, _ in files:
        if approx >= bytes_cap_total:
            break
        try:
            with open(fp, 'rb') as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                start = max(0, size - int(bytes_cap_per_file))
                fh.seek(start, os.SEEK_SET)
                chunk = fh.read(int(bytes_cap_per_file))
            text = chunk.decode('utf-8', errors='ignore')
            lines.extend(text.splitlines())
            approx += len(text)
        except OSError:
            continue
    return lines


def extract_last_error_text(last_analysis: str, log_files: List[str]) -> str:
    """Return the most recent error-like line from analysis or logs.

    Preference: last_analysis content; fallback: tail of provided log files.
    """
    hit = ""
    err_pat = re.compile(r"(error|exception|failed|timeout|fatal|panic|\b5\d{2}\b)", re.IGNORECASE)
    # try analysis first
    if last_analysis:
        for line in reversed(last_analysis.splitlines()):
            if err_pat.search(line):
                hit = line.strip()
                break
    if hit:
        return hit

    # fallback: scan tail of logs
    for ln in reversed(_tail_lines(log_files)):
        if err_pat.search(ln):
            return ln.strip()
    return "(no recent error found)"


def _fallback_mmm(last_error: str, persona: str = "developer") -> Tuple[str, str, str]:
    text = last_error or ""
    tl = text.lower()

    # Heuristics
    if "declin" in tl:
        mirror = "Payments are being declined; user cannot complete checkout."
        mentor_dev = "Reproduce with test card; log gateway response; branch on decline codes."
        multiplier = "Standardize decline handling and user messaging across services."
    elif "timeout" in tl or "timed out" in tl or "latency" in tl:
        mirror = "Requests are timing out; downstream service latency is high."
        mentor_dev = "Add timeouts/retries; instrument slow calls; review circuit-breakers."
        multiplier = "Adopt service-level timeouts and shared retry/backoff policy."
    elif "pay_" in tl or "payment" in tl:
        mirror = "Payment flow is erroring consistently in recent attempts."
        mentor_dev = "Trace payment path; check gateway config and error code mapping."
        multiplier = "Create a payment failure playbook and alert on error spikes."
    elif "5" in tl and "error" in tl:
        mirror = "Server-side errors are occurring in the last operation."
        mentor_dev = "Inspect server logs/trace; add guards around failing endpoint."
        multiplier = "Harden error boundaries and propagate actionable codes."
    else:
        mirror = "A recurring issue is visible in the latest operation."
        mentor_dev = "Capture minimal repro; add observability for the failing step."
        multiplier = "Document fix pattern and roll it into shared guidelines."

    persona = (persona or "developer").lower()
    if persona in ("dev", "developer", "ic"):
        mentor = mentor_dev
    elif persona in ("lead", "manager"):
        mentor = "Triage blast radius; assign owner; set SLO and mitigation window."
    elif persona in ("exec", "executive"):
        mentor = "Prioritize reliability work; align teams on concrete risk-reduction steps."
    elif persona in ("agent",):
        mentor = "Auto-create issue, attach logs, propose patch, and request review."
    else:
        mentor = mentor_dev

    return mirror, mentor, multiplier


def generate_mmm(last_error: str, persona: str = "developer", ollama_url: Optional[str] = None, model: Optional[str] = None) -> Tuple[str, str, str]:
    """Generate Mirror/Mentor/Multiplier lines, with safe local fallback.

    If Ollama is available, attempt a short refinement; otherwise return fallback.
    """
    mirror, mentor, multiplier = _fallback_mmm(last_error, persona)

    if not (ollama_url and model and ask_llama):
        return mirror, mentor, multiplier

    # Tiny refinement prompt (kept small/deterministic)
    prompt = (
        "You produce 3 terse lines given an error.\n"
        f"Error: {last_error[:400]}\n"
        f"Persona: {persona}\n"
        "Return as: Mirror|Mentor|Multiplier. 12-16 words each. No extra text."
    )
    try:
        resp = ask_llama(ollama_url, model, prompt)  # type: ignore
        text = str(resp)
        # parse simple "Mirror|Mentor|Multiplier"
        parts = [p.strip() for p in text.split('|')]
        if len(parts) >= 3 and all(parts[:3]):
            return parts[0], parts[1], parts[2]
    except Exception:
        pass
    return mirror, mentor, multiplier



