"""Receipt management module for AI Log Helper.

This module handles saving analysis receipts to JSONL files.
"""
import os
import json
import time

RECEIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "receipts")

def write_receipt(obj: dict) -> str:
    """Write analysis receipt to JSONL file."""
    os.makedirs(RECEIPTS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RECEIPTS_DIR, f"{ts}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return path
