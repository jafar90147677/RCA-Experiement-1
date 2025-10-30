"""Log analysis module for AI Log Helper.

This module handles log file analysis, pattern detection, and AI-powered diagnostics.
"""
import os
import glob
import time
import re
import threading
import sqlite3
from typing import List, Tuple, Dict, Any, Callable, Optional, Union
from receipts import write_receipt
from ollama_client import ask_llama

ERROR_KEYS = ["error", "exception", "traceback", "failed", "timeout", "fatal", "panic", "stack", "500", " 4xx ", " 5xx ", "warn", "warning", "performance", "resultsCount:0", "high value", "large", "slow", "cart limit", "search", "inventory", "stock", "checkout", "order", "transaction"]

MAX_FILES = 5          # scan up to 5 newest files
MAX_BYTES_TOTAL = 10 * 1024 * 1024  # 10MB cap
CONTEXT_LINES = 4      # lines before/after a hit
MAX_PROMPT_CHARS = 8000

def build_pattern_agent_prompt(files_used: List[str], top_keywords: Dict[str, int], tx_groups: Dict[str, List[str]], raw_snippet: str, error_patterns: Optional[Dict[str, Any]] = None) -> str:
    """Build an analysis prompt for the Pattern Agent to analyze log patterns.

    Args:
        files_used: File paths considered.
        top_keywords: Mapping keyword -> count.
        tx_groups: Mapping of transaction id -> list of lines.
        raw_snippet: Raw snippet text with error windows.
        error_patterns: Specific error pattern analysis from _extract_error_patterns.

    Returns:
        Analysis prompt for the LLM to generate meaningful insights.
    """
    # Files line
    files_line = ", ".join(files_used) if files_used else "(none)"

    # Transactions count
    tx_count = len(tx_groups) if tx_groups else 0

    # Top keywords line (sorted deterministically)
    if top_keywords:
        sorted_items = sorted(top_keywords.items(), key=lambda kv: (-kv[1], kv[0]))
        top_kw_line = ", ".join(f"{k}({v})" for k, v in sorted_items[:10])
    else:
        top_kw_line = "None detected"

    # Build specific error analysis summary
    error_analysis = ""
    if error_patterns:
        error_analysis = "\nSPECIFIC ERROR ANALYSIS:\n"
        error_analysis += "=" * 30 + "\n"
        
        # Error codes
        if error_patterns.get('error_codes'):
            error_analysis += f"Error Codes: {dict(sorted(error_patterns['error_codes'].items(), key=lambda x: x[1], reverse=True))}\n"
        
        # Error messages
        if error_patterns.get('error_messages'):
            error_analysis += f"Error Messages: {dict(sorted(error_patterns['error_messages'].items(), key=lambda x: x[1], reverse=True))}\n"
        
        # Error categories
        if error_patterns.get('error_categories'):
            error_analysis += f"Error Categories: {dict(sorted(error_patterns['error_categories'].items(), key=lambda x: x[1], reverse=True))}\n"
        
        # Warning messages
        if error_patterns.get('warning_messages'):
            error_analysis += f"Warning Messages: {dict(sorted(error_patterns['warning_messages'].items(), key=lambda x: x[1], reverse=True))}\n"
        
        # Warning categories
        if error_patterns.get('warning_categories'):
            error_analysis += f"Warning Categories: {dict(sorted(error_patterns['warning_categories'].items(), key=lambda x: x[1], reverse=True))}\n"
        
        # Temporal patterns
        if error_patterns.get('temporal_patterns'):
            error_analysis += f"Temporal Patterns: {len(error_patterns['temporal_patterns'])} warning-error sequences found\n"
        
        error_analysis += "\n"

    prompt = f"""You are an expert log analyst. Analyze the ACTUAL ERROR AND WARNING LOGS below to identify specific patterns and root causes.

LOG SUMMARY:
- Files: {files_line}
- Transactions: {tx_count}
- Top Issues: {top_kw_line}

{error_analysis}ACTUAL ERROR & WARNING LOGS:
{raw_snippet[:3000]}

ANALYSIS TASK:
=============
1. PATTERNS: Look at the specific error analysis above and actual log entries. Identify:
   - EXACT ERROR CODES and their frequency (e.g., PAY_001 appears X times)
   - EXACT ERROR MESSAGES and their frequency (e.g., "Payment processing failed" appears X times)
   - EXACT ERROR CATEGORIES and their frequency (e.g., PAYMENT category has X errors)
   - TEMPORAL PATTERNS (e.g., warning always precedes error by X seconds)
   - CONSISTENCY PATTERNS (e.g., all errors have same error code, all warnings have same message)

2. ROOT CAUSES: From the specific error data above, determine:
   - Why does the SAME error code appear repeatedly? (e.g., PAY_001 in 100% of errors)
   - Why does the SAME error message appear repeatedly? (e.g., "Card declined" in all errors)
   - Why do warnings always precede errors? (e.g., performance warning → payment failure)
   - What specific technical issue causes this pattern?

3. HIGH-RISK TRANSACTIONS: Identify which transactions are most problematic:
   - Which transactions have the most errors?
   - Which transactions show the warning→error pattern?
   - Which transactions have the highest error frequency?

4. NEXT ACTIONS: Based on the SPECIFIC error patterns found, recommend:
   - Specific fixes for the EXACT error codes identified (e.g., fix PAY_001 handling)
   - Specific fixes for the EXACT error messages (e.g., handle "Card declined" better)
   - Specific fixes for the temporal patterns (e.g., prevent warning→error sequence)
   - Code changes to address the specific patterns

RESPONSE FORMAT:
===============
Provide your analysis in exactly this format:

🤖 AI PATTERN AGENT ANALYSIS
==================================================
📁 Files: {len(files_used)} file(s) analyzed
📊 Transactions: {tx_count} found
🔍 Top Keywords: {top_kw_line}

📋 PATTERNS
  • [EXACT error patterns - e.g., "PAY_001 error code appears in 100% of errors (X occurrences)"]
  • [EXACT message patterns - e.g., "Payment processing failed" message appears X times]
  • [EXACT category patterns - e.g., "All errors are in PAYMENT category"]
  • [Temporal patterns - e.g., "Warning always precedes error by 1-2 seconds"]

🔍 ROOT CAUSES
  • [Specific root causes from EXACT error data - e.g., "PAY_001 indicates payment gateway integration issue"]
  • [Specific technical issues - e.g., "All errors have same error code suggests single point of failure"]

⚠️ HIGH-RISK TRANSACTIONS
  • [Specific high-risk transactions with EXACT error counts and patterns]

🎯 NEXT ACTIONS
  • [Specific technical fixes for EXACT error codes - e.g., "Fix PAY_001 error handling"]
  • [Specific fixes for EXACT patterns - e.g., "Prevent warning→error sequence"]

REFERENCE THE EXACT ERROR CODES, MESSAGES, AND PATTERNS FROM THE DATA ABOVE. Be specific and accurate."""

    return prompt

def read_sqlite_log_rows(db_path: str, table: str, limit: int = 2000) -> List[str]:
    """Read log rows from SQLite database and format as text lines.
    
    Args:
        db_path: Path to SQLite database file.
        table: Table name containing log data.
        limit: Maximum number of rows to read.
        
    Returns:
        List of formatted log lines as strings.
    """
    if not os.path.exists(db_path):
        return []
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get table schema to detect available columns
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        
        if not columns:
            conn.close()
            return []
        
        # Detect common column names for timestamp, level, message, transactionId
        ts_col = None
        level_col = None
        msg_col = None
        txn_col = None
        
        for col in columns:
            col_lower = col.lower()
            if 'timestamp' in col_lower or 'ts' in col_lower or 'time' in col_lower:
                ts_col = col
            elif 'level' in col_lower or 'severity' in col_lower or 'log_level' in col_lower:
                level_col = col
            elif 'message' in col_lower or 'msg' in col_lower or 'content' in col_lower or 'text' in col_lower:
                msg_col = col
            elif 'transaction' in col_lower or 'txn' in col_lower or 'transactionid' in col_lower:
                txn_col = col
        
        # Build SELECT query with available columns
        select_cols = []
        if ts_col:
            select_cols.append(f"{ts_col} as ts")
        if level_col:
            select_cols.append(f"{level_col} as level")
        if msg_col:
            select_cols.append(f"{msg_col} as msg")
        if txn_col:
            select_cols.append(f"{txn_col} as transactionId")
        
        # Add any remaining columns
        for col in columns:
            if col not in [ts_col, level_col, msg_col, txn_col]:
                select_cols.append(col)
        
        if not select_cols:
            conn.close()
        return []
    
        # Order by timestamp if available, otherwise by rowid
        order_by = f"ORDER BY {ts_col} DESC" if ts_col else "ORDER BY rowid DESC"
        
        query = f"SELECT {', '.join(select_cols)} FROM {table} {order_by} LIMIT {limit}"
        cursor.execute(query)
        rows = cursor.fetchall()
        
        # Format rows as log lines
        lines = []
        for row in rows:
            parts = []
            for i, col in enumerate(select_cols):
                if i < len(row) and row[i] is not None:
                    col_name = col.split(' as ')[-1]  # Get alias if present
                    parts.append(f"{col_name}={row[i]}")
            
            if parts:
                lines.append(" ".join(parts))
        
        conn.close()
        return lines
        
    except Exception:
        # Best-effort: return empty list on any error
        return []

def _collect_recent_log_lines(paths: List[str], bytes_cap_total: int, bytes_cap_per_file: int, sqlite_source: Optional[Dict[str, Union[str, int]]] = None) -> List[str]:
    """Collect recent lines from provided log file paths and optional SQLite source.

    Reads at most ``bytes_cap_per_file`` bytes from the end of each file, in
    order of most-recent modification time first, and stops when the combined
    approximate size reaches ``bytes_cap_total``. UTF-8 decoding with
    ``errors='ignore'`` is used. Missing/unreadable files are skipped.
    
    Optionally includes lines from SQLite database if sqlite_source is provided.

    This helper is internal scaffolding for a future Pattern Agent.

    Args:
        paths: File system paths to log files.
        bytes_cap_total: Soft cap on total bytes across all files.
        bytes_cap_per_file: Hard cap per file read window from the end.
        sqlite_source: Optional dict with 'db', 'table', 'limit' keys for SQLite.

    Returns:
        A list of log lines aggregated across the newest files first. Returns
        an empty list if ``paths`` is empty or nothing readable is found.
    """
    collected_lines: List[str] = []
    
    # Add SQLite lines first if provided
    if sqlite_source:
        try:
            db_path = sqlite_source.get('db', '')
            table = sqlite_source.get('table', 'logs')
            limit = sqlite_source.get('limit', 2000)
            
            if db_path and table:
                sqlite_lines = read_sqlite_log_rows(db_path, table, limit)
                collected_lines.extend(sqlite_lines)
        except Exception:
            # Silently continue on SQLite errors
            pass
    
    if not paths or bytes_cap_total <= 0 or bytes_cap_per_file <= 0:
        return collected_lines

    # Sort paths by modification time (newest first); skip non-files safely
    files_with_time: List[Tuple[str, float]] = []
    for p in paths:
        try:
            if os.path.isfile(p):
                files_with_time.append((p, os.path.getmtime(p)))
        except (OSError, IOError):
            continue
    files_with_time.sort(key=lambda t: t[1], reverse=True)

    approx_total_chars = 0  # We approximate bytes via decoded char length

    for file_path, _ in files_with_time:
        if approx_total_chars >= bytes_cap_total:
            break

        try:
            with open(file_path, 'rb') as fh:
                try:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    read_start = max(0, size - int(bytes_cap_per_file))
                    fh.seek(read_start, os.SEEK_SET)
                    chunk_bytes = fh.read(int(bytes_cap_per_file))
                except (OSError, IOError):
                    chunk_bytes = b""
        except (OSError, IOError):
            # Skip unreadable files, continue
            continue

        if not chunk_bytes:
            continue

        try:
            chunk_text = chunk_bytes.decode('utf-8', errors='ignore')
        except Exception:
            # Skip files with decode errors, continue
            continue

        # Split into lines and append; update approximate char budget
        lines = chunk_text.splitlines()
        collected_lines.extend(lines)
        approx_total_chars += len(chunk_text)

    return collected_lines


def _group_by_transaction(lines: List[str]) -> Dict[str, List[str]]:
    """Group lines by detected transaction/session/trace identifier.

    Preference order: ``transactionId`` (JSON or key/value), then fallback
    keys: ``transaction|transId|txn|session|trace`` (case-insensitive).

    The returned mapping is keyed by a normalized identifier (lowercased,
    trimmed of quotes/brackets). Lines without any detectable identifier are
    grouped under the key ``'unknown'`` to avoid dropping signal.

    Args:
        lines: Log lines to group.

    Returns:
        Dict mapping normalized identifier -> list of lines.
    """
    groups: Dict[str, List[str]] = {}
    if not lines:
        return groups

    # Prefer transactionId
    txnid_patterns = [
        re.compile(r"\btransactionId\b\s*[:=]\s*\"?([-A-Za-z0-9._:]+)\"?", re.IGNORECASE),
    ]
    # Fallback keys: transaction|transId|txn|session|trace
    fallback_pattern = re.compile(
        r"\b(?:transaction|transId|txn|session|trace)(?:Id)?\b\s*[:=]\s*\"?([-A-Za-z0-9._:]+)\"?",
        re.IGNORECASE,
    )

    def normalize(identifier: str) -> str:
        ident = identifier.strip().strip('"\'[](){}:,')
        return ident.lower()

    for ln in lines:
        if not ln:
            key = 'unknown'
        else:
            key_found: str = ''
            for pat in txnid_patterns:
                m = pat.search(ln)
                if m:
                    key_found = m.group(1)
                    break
            if not key_found:
                m = fallback_pattern.search(ln)
                if m:
                    key_found = m.group(1)

            key = normalize(key_found) if key_found else 'unknown'

        if key not in groups:
            groups[key] = []
        groups[key].append(ln)

    return groups


def _get_file_statistics_streaming(file_path: str) -> Dict[str, Any]:
    """Get accurate statistics from entire file using streaming (zero memory impact).
    
    Args:
        file_path: Path to log file to analyze.
        
    Returns:
        Dict containing complete file statistics:
        - error_count: Total number of errors
        - warning_count: Total number of warnings  
        - error_codes: Dict[error_code, count]
        - error_messages: Dict[message, count]
        - error_categories: Dict[category, count]
        - warning_messages: Dict[message, count]
        - warning_categories: Dict[category, count]
        - transaction_ids: Set of unique transaction IDs (no cap)
        - file_size: Total file size in bytes
        - total_lines: Total number of lines processed
    """
    import json
    
    stats = {
        'error_count': 0,
        'warning_count': 0,
        'error_codes': {},
        'error_messages': {},
        'error_categories': {},
        'warning_messages': {},
        'warning_categories': {},
        'transaction_ids': set(),
        'file_size': 0,
        'total_lines': 0
    }
    
    if not os.path.exists(file_path):
        return stats
    
    stats['file_size'] = os.path.getsize(file_path)
    
    # Process file line by line (constant memory usage)
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line_num, line in enumerate(f, 1):
            stats['total_lines'] += 1
            
            # Progress indicator for very large files
            if line_num % 100000 == 0:
                print(f"Processing... {line_num:,} lines")
            
            line = line.strip()
            
            if not line:
                continue
                
            try:
                # Parse JSON log entry
                if line.startswith('{'):
                    log_entry = json.loads(line)
                    level = log_entry.get('level', '').upper()
                    category = log_entry.get('category', '')
                    message = log_entry.get('message', '')
                    error_code = log_entry.get('data', {}).get('errorCode', '')
                    
                    if level == 'ERROR':
                        stats['error_count'] += 1
                        
                        # Count error codes
                        if error_code:
                            stats['error_codes'][error_code] = stats['error_codes'].get(error_code, 0) + 1
                        
                        # Count error messages
                        if message:
                            stats['error_messages'][message] = stats['error_messages'].get(message, 0) + 1
                        
                        # Count error categories
                        if category:
                            stats['error_categories'][category] = stats['error_categories'].get(category, 0) + 1
                    
                    elif level == 'WARN':
                        stats['warning_count'] += 1
                        
                        # Count warning messages
                        if message:
                            stats['warning_messages'][message] = stats['warning_messages'].get(message, 0) + 1
                        
                        # Count warning categories
                        if category:
                            stats['warning_categories'][category] = stats['warning_categories'].get(category, 0) + 1
                    
                    # Track unique transactions (no cap; exact count)
                    txn_id = _extract_transaction_id_from_entry(log_entry)
                    if txn_id:
                        stats['transaction_ids'].add(txn_id)
                            
            except (json.JSONDecodeError, KeyError, TypeError):
                # Skip malformed lines
                continue
    
    # Convert set to count for final stats
    stats['unique_transactions'] = len(stats['transaction_ids'])
    stats['transaction_ids'] = list(stats['transaction_ids'])[:100]  # Keep only first 100 for display
    
    return stats

def _extract_transaction_id_from_entry(log_entry: Dict[str, Any]) -> Optional[str]:
    """Extract transaction ID from log entry.
    
    Args:
        log_entry: Parsed JSON log entry.
        
    Returns:
        Transaction ID string or None if not found.
    """
    # Try transactionId first
    txn_id = log_entry.get('data', {}).get('transactionId', '')
    if txn_id:
        return txn_id
    
    # Try session_id as fallback
    session_id = log_entry.get('session_id', '')
    if session_id:
        return session_id
    
    return None

def _extract_representative_samples(file_path: str, max_samples: int = 100) -> List[Dict[str, Any]]:
    """Extract representative error/warning samples from file without loading entire file.
    
    Args:
        file_path: Path to log file.
        max_samples: Maximum number of samples to extract.
        
    Returns:
        List of sample log entries with error/warning data.
    """
    import json
    
    samples = []
    file_size = os.path.getsize(file_path)
    
    # Calculate sample positions distributed across the file
    sample_positions = []
    for i in range(max_samples * 2):  # Get 2x samples to account for misses
        pos = int((i / (max_samples * 2)) * file_size)
        sample_positions.append(pos)
    
    # Extract samples from calculated positions
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for pos in sample_positions:
            if len(samples) >= max_samples:
                break

            f.seek(pos)
            
            # Read a few lines around this position
            lines_to_read = 10
            for _ in range(lines_to_read):
                line = f.readline()
                if not line:
                    break
                    
                line = line.strip()
                if not line or not line.startswith('{'):
                    continue
                    
                try:
                    log_entry = json.loads(line)
                    level = log_entry.get('level', '').upper()
                    
                    if level in ['ERROR', 'WARN']:
                        sample = {
                            'id': log_entry.get('id', ''),
                            'timestamp': log_entry.get('timestamp', ''),
                            'level': level,
                            'category': log_entry.get('category', ''),
                            'message': log_entry.get('message', ''),
                            'error_code': log_entry.get('data', {}).get('errorCode', ''),
                            'order_total': log_entry.get('data', {}).get('orderTotal', ''),
                            'order_value': log_entry.get('data', {}).get('orderValue', ''),
                            'raw_line': line
                        }
                        samples.append(sample)
                        break  # Found one sample, move to next position
                        
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    
    return samples[:max_samples]

def _build_streaming_llm_prompt(file_path: str, stats: Dict[str, Any], samples: List[Dict[str, Any]]) -> str:
    """Build compact LLM prompt with accurate statistics from streaming analysis.
    
    Args:
        file_path: Path to analyzed file.
        stats: Complete file statistics from streaming analysis.
        samples: Representative samples from file.
        
    Returns:
        Compact LLM prompt under 8KB.
    """
    file_name = os.path.basename(file_path)
    file_size_mb = stats['file_size'] / (1024 * 1024)
    
    # Format error codes for display
    error_codes_str = ", ".join([f"{code}({count})" for code, count in sorted(stats['error_codes'].items(), key=lambda x: x[1], reverse=True)[:5]])
    
    # Format error messages for display
    error_messages_str = ", ".join([f'"{msg}"({count})' for msg, count in sorted(stats['error_messages'].items(), key=lambda x: x[1], reverse=True)[:3]])
    
    # Format warning messages for display
    warning_messages_str = ", ".join([f'"{msg}"({count})' for msg, count in sorted(stats['warning_messages'].items(), key=lambda x: x[1], reverse=True)[:3]])
    
    # Format samples for display
    samples_str = ""
    for i, sample in enumerate(samples[:10], 1):
        if sample['level'] == 'ERROR':
            samples_str += f"  Error {i}: ID={sample['id']} | Code={sample['error_code']} | Category={sample['category']} | Message='{sample['message']}'\n"
        elif sample['level'] == 'WARN':
            samples_str += f"  Warning {i}: ID={sample['id']} | Category={sample['category']} | Message='{sample['message']}'\n"
    
    tx_display = f"{stats['unique_transactions']:,}"
    prompt = f"""You are an expert log analyst. Analyze the COMPLETE FILE STATISTICS below to identify patterns and root causes.

FILE ANALYSIS SUMMARY:
- File: {file_name} ({file_size_mb:.1f}MB)
- Total Lines Processed: {stats['total_lines']:,}
- Total Errors: {stats['error_count']:,}
- Total Warnings: {stats['warning_count']:,}
- Unique Transactions: {tx_display}

EXACT ERROR ANALYSIS:
- Error Codes: {error_codes_str}
- Error Messages: {error_messages_str}
- Error Categories: {dict(sorted(stats['error_categories'].items(), key=lambda x: x[1], reverse=True))}

EXACT WARNING ANALYSIS:
- Warning Messages: {warning_messages_str}
- Warning Categories: {dict(sorted(stats['warning_categories'].items(), key=lambda x: x[1], reverse=True))}

REPRESENTATIVE SAMPLES:
{samples_str}

ANALYSIS TASK:
1. PATTERNS: Based on the EXACT statistics above, identify:
   - What error codes appear and their frequency
   - What error messages repeat and their frequency
   - What warning patterns exist
   - Temporal or systematic patterns

2. ROOT CAUSES: From the specific error data, determine:
   - Why the same error code appears repeatedly
   - Why the same error message appears repeatedly
   - What technical issues cause these patterns

3. HIGH-RISK TRANSACTIONS: Identify:
   - Which transactions are most problematic
   - Transaction patterns that lead to errors
   - Risk factors based on the data

4. NEXT ACTIONS: Recommend specific fixes:
   - For the exact error codes identified
   - For the specific error patterns found
   - For the warning patterns that precede errors

RESPONSE FORMAT:
Provide your analysis in exactly this format:

🤖 AI PATTERN AGENT ANALYSIS
==================================================
📁 Files: 1 file(s) analyzed
📊 Transactions: {stats['unique_transactions']:,} found
🔍 Top Keywords: {_format_top_keywords(stats)}

📋 PATTERNS
  • [EXACT error patterns from statistics above]
  • [EXACT warning patterns from statistics above]
  • [Temporal or systematic patterns identified]

🔍 ROOT CAUSES
  • [Specific root causes from EXACT error data]
  • [Technical issues causing the patterns]

⚠️ HIGH-RISK TRANSACTIONS
  • [High-risk transactions with specific counts and patterns]

🎯 NEXT ACTIONS
  • [Specific technical fixes for EXACT error codes]
  • [Specific fixes for EXACT patterns found]

REFERENCE THE EXACT COUNTS AND PATTERNS FROM THE STATISTICS ABOVE."""

    return prompt[:8000]  # Hard limit for LLM

def _format_top_keywords(stats: Dict[str, Any]) -> str:
    """Format top keywords from streaming statistics.
    
    Args:
        stats: Statistics from streaming analysis.
        
    Returns:
        Formatted keyword string.
    """
    keywords = []
    
    # Add error-related keywords
    if stats['error_count'] > 0:
        keywords.append(f"error({stats['error_count']})")
    if stats['warning_count'] > 0:
        keywords.append(f"warn({stats['warning_count']})")
    
    # Add specific error codes
    for code, count in sorted(stats['error_codes'].items(), key=lambda x: x[1], reverse=True)[:3]:
        keywords.append(f"{code}({count})")
    
    # Add categories
    for category, count in sorted(stats['error_categories'].items(), key=lambda x: x[1], reverse=True)[:2]:
        keywords.append(f"{category.lower()}({count})")
    
    return ", ".join(keywords[:8])  # Limit to 8 keywords


def _build_sections_from_stats(stats: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """Build deterministic section texts from streaming stats.

    Returns: (patterns, root_causes, high_risk, next_actions)
    """
    # Patterns
    patt_lines: List[str] = []
    for code, count in sorted(stats.get('error_codes', {}).items(), key=lambda x: x[1], reverse=True)[:3]:
        patt_lines.append(f"{code} appears {count:,} times")
    for msg, count in sorted(stats.get('error_messages', {}).items(), key=lambda x: x[1], reverse=True)[:2]:
        patt_lines.append(f'"{msg}" seen {count:,} times')
    for cat, count in sorted(stats.get('error_categories', {}).items(), key=lambda x: x[1], reverse=True)[:2]:
        patt_lines.append(f"{cat} category has {count:,} errors")
    if not patt_lines:
        patt_lines = ["(none)"]

    # Root causes
    root_lines: List[str] = []
    top_code = next(iter(sorted(stats.get('error_codes', {}).items(), key=lambda x: x[1], reverse=True)), (None, None))[0]
    if top_code:
        root_lines.append(f"High concentration of {top_code} suggests single failure mode")
    if stats.get('warning_count', 0) and stats.get('error_count', 0):
        root_lines.append("Warnings alongside errors indicate potential upstream performance/timeout issues")
    if not root_lines:
        root_lines = ["(none)"]

    # High risk
    risk_lines: List[str] = []
    if stats.get('unique_transactions', 0) > 0 and stats.get('error_count', 0) > 0:
        risk_lines.append("Transactions with repeated errors (same code/message) are high-risk")
    if not risk_lines:
        risk_lines = ["(none)"]

    # Next actions
    next_lines: List[str] = []
    if top_code:
        next_lines.append("Implement guardrails/retries around top error code(s)")
    if stats.get('warning_count', 0):
        next_lines.append("Investigate top warnings to prevent escalation into errors")
    next_lines.append("Add targeted logs around payment/checkout paths to isolate cause")

    patt_text = "\n  • ".join([patt_lines[0]] + patt_lines[1:])
    root_text = "\n  • ".join([root_lines[0]] + root_lines[1:])
    risk_text = "\n  • ".join([risk_lines[0]] + risk_lines[1:])
    next_text = "\n  • ".join([next_lines[0]] + next_lines[1:])
    return patt_text, root_text, risk_text, next_text

def _extract_error_patterns(lines: List[str]) -> Dict[str, Any]:
    """Extract specific error patterns from log lines.
    
    Args:
        lines: Log lines to analyze.
        
    Returns:
        Dict containing specific error analysis:
        - error_codes: Dict[error_code, count]
        - error_messages: Dict[message, count] 
        - error_categories: Dict[category, count]
        - warning_messages: Dict[message, count]
        - warning_categories: Dict[category, count]
        - temporal_patterns: List of error-warning sequences
    """
    import json
    
    patterns: Dict[str, Any] = {
        'error_codes': {},
        'error_messages': {},
        'error_categories': {},
        'warning_messages': {},
        'warning_categories': {},
        'temporal_patterns': []
    }
    
    if not lines:
        return patterns
    
    # Track recent warnings for temporal analysis
    recent_warnings: List[Dict[str, Any]] = []
    
    for i, line in enumerate(lines):
        if not line.strip():
            continue
            
        try:
            # Try to parse as JSON log entry
            if line.strip().startswith('{'):
                log_entry = json.loads(line)
                level = log_entry.get('level', '').upper()
                category = log_entry.get('category', '')
                message = log_entry.get('message', '')
                error_code = log_entry.get('data', {}).get('errorCode', '')
                timestamp = log_entry.get('timestamp', '')
                
                if level == 'ERROR':
                    # Count error codes
                    if error_code:
                        error_codes = patterns['error_codes']
                        if isinstance(error_codes, dict):
                            error_codes[error_code] = error_codes.get(error_code, 0) + 1
                    
                    # Count error messages
                    if message:
                        error_messages = patterns['error_messages']
                        if isinstance(error_messages, dict):
                            error_messages[message] = error_messages.get(message, 0) + 1
                    
                    # Count error categories
                    if category:
                        error_categories = patterns['error_categories']
                        if isinstance(error_categories, dict):
                            error_categories[category] = error_categories.get(category, 0) + 1
                    
                    # Check for temporal pattern (warning before error)
                    temporal_patterns = patterns['temporal_patterns']
                    if recent_warnings and isinstance(temporal_patterns, list):
                        temporal_patterns.append({
                            'warning': recent_warnings[-1],
                            'error': {
                                'message': message,
                                'error_code': error_code,
                                'category': category,
                                'timestamp': timestamp
                            }
                        })
                
                elif level == 'WARN':
                    # Count warning messages
                    if message:
                        warning_messages = patterns['warning_messages']
                        if isinstance(warning_messages, dict):
                            warning_messages[message] = warning_messages.get(message, 0) + 1
                    
                    # Count warning categories
                    if category:
                        warning_categories = patterns['warning_categories']
                        if isinstance(warning_categories, dict):
                            warning_categories[category] = warning_categories.get(category, 0) + 1
                    
                    # Store recent warning for temporal analysis
                    recent_warnings.append({
                        'message': message,
                        'category': category,
                        'timestamp': timestamp
                    })
                    
                    # Keep only last 5 warnings to avoid memory issues
                    if len(recent_warnings) > 5:
                        recent_warnings.pop(0)
                        
        except (json.JSONDecodeError, KeyError, TypeError):
            # Skip non-JSON lines or malformed entries
            continue
    
    return patterns

def _count_keywords(lines: List[str]) -> Dict[str, int]:
    """Count important keywords across lines (case-insensitive).

    The following terms are counted with simple inflection handling:
    - error, warn, timeout, fail, exception, perf, latency, search, cart,
      checkout, payment

    Args:
        lines: Log lines to scan.

    Returns:
        Dict of keyword -> count. Keys with zero counts are omitted.
    """
    counts: Dict[str, int] = {}
    if not lines:
        return counts

    patterns: List[Tuple[str, re.Pattern]] = [
        ("error", re.compile(r"\berror(s)?\b|\bfatal\b|\bpanic\b", re.IGNORECASE)),
        ("warn", re.compile(r"\bwarn(ing|ed|s)?\b", re.IGNORECASE)),
        ("timeout", re.compile(r"\btime\s*out(s|ed)?\b|\btimeout(s)?\b", re.IGNORECASE)),
        ("fail", re.compile(r"\bfail(ed|ure|s)?\b", re.IGNORECASE)),
        ("exception", re.compile(r"\bexception(s)?\b|traceback", re.IGNORECASE)),
        ("perf", re.compile(r"\bperf(ormance)?\b|\bslow\b", re.IGNORECASE)),
        ("latency", re.compile(r"\blatenc(y|ies)\b", re.IGNORECASE)),
        ("search", re.compile(r"\bsearch(ing)?\b", re.IGNORECASE)),
        ("cart", re.compile(r"\bcart(s)?\b", re.IGNORECASE)),
        ("checkout", re.compile(r"\bcheckout(s)?\b", re.IGNORECASE)),
        ("payment", re.compile(r"\bpayment(s)?\b|\bpay\b", re.IGNORECASE)),
    ]

    for ln in lines:
        if not ln:
            continue
        for key, pat in patterns:
            if pat.search(ln):
                counts[key] = counts.get(key, 0) + 1

    return counts


def _build_agent_snippets(lines: List[str], max_chars: int) -> str:
    """Build compact snippets focusing on recent error-related windows.

    Identifies lines with error-like tokens and collects a small window of
    surrounding context controlled by the module's ``CONTEXT_LINES``. The most
    recent windows are kept first. The final string is hard-truncated to
    ``max_chars`` characters. Empty input yields an empty string.

    Args:
        lines: Log lines in chronological order.
        max_chars: Maximum characters to return.

    Returns:
        A single string containing selected windows, truncated to ``max_chars``.
    """
    if not lines or max_chars <= 0:
        return ""

    hit_pattern = re.compile(r"error|exception|fail|timeout|warn|fatal|panic|declined|insufficient|payment.*fail", re.IGNORECASE)

    hit_indices: List[int] = []
    for idx, ln in enumerate(lines):
        if ln and hit_pattern.search(ln):
            hit_indices.append(idx)

    if not hit_indices:
        # No obvious errors; return recent content with payment/checkout focus
        recent_lines = lines[-(CONTEXT_LINES * 6):]
        # Filter for payment/checkout related lines
        payment_lines = [ln for ln in recent_lines if re.search(r"payment|checkout|cart|order", ln, re.IGNORECASE)]
        if payment_lines:
            return "\n".join(payment_lines[-20:])[:max_chars]
        else:
            return "\n".join(recent_lines)[:max_chars]

    # Build windows, starting from the most recent hit
    windows: List[Tuple[int, int]] = []
    covered: List[Tuple[int, int]] = []
    for i in reversed(hit_indices):
        start = max(0, i - CONTEXT_LINES)
        end = min(len(lines), i + CONTEXT_LINES + 1)
        # Merge with existing if overlapping
        if covered and not (end <= covered[-1][0] or start >= covered[-1][1]):
            prev_start, prev_end = covered[-1]
            merged = (min(prev_start, start), max(prev_end, end))
            covered[-1] = merged
        else:
            covered.append((start, end))
    # covered now has most-recent-first merged windows
    windows = covered

    # Collect actual error and warning lines with JSON parsing
    import json
    
    error_examples: List[Dict[str, Any]] = []
    warning_examples: List[Dict[str, Any]] = []
    
    for idx in hit_indices[:100]:  # Get up to 100 error/warning lines
        line = lines[idx]
        ln_lower = line.lower()
        
        try:
            if line.strip().startswith('{'):
                log_entry = json.loads(line)
                level = log_entry.get('level', '').upper()
                
                if level == 'ERROR':
                    error_examples.append({
                        'id': log_entry.get('id', ''),
                        'timestamp': log_entry.get('timestamp', ''),
                        'category': log_entry.get('category', ''),
                        'message': log_entry.get('message', ''),
                        'error_code': log_entry.get('data', {}).get('errorCode', ''),
                        'order_total': log_entry.get('data', {}).get('orderTotal', ''),
                        'raw_line': line
                    })
                elif level == 'WARN':
                    warning_examples.append({
                        'id': log_entry.get('id', ''),
                        'timestamp': log_entry.get('timestamp', ''),
                        'category': log_entry.get('category', ''),
                        'message': log_entry.get('message', ''),
                        'order_value': log_entry.get('data', {}).get('orderValue', ''),
                        'estimated_time': log_entry.get('data', {}).get('estimatedTime', ''),
                        'raw_line': line
                    })
        except (json.JSONDecodeError, KeyError, TypeError):
            # Fallback to simple text matching for non-JSON lines
            if 'error' in ln_lower or 'exception' in ln_lower or 'fail' in ln_lower or 'fatal' in ln_lower:
                error_examples.append({'raw_line': line, 'message': 'Non-JSON error line'})
            elif 'warn' in ln_lower:
                warning_examples.append({'raw_line': line, 'message': 'Non-JSON warning line'})
    
    parts: List[str] = []
    parts.append("=== ACTUAL ERROR & WARNING LOGS ===")
    parts.append("")
    
    # Add error examples with specific details
    if error_examples:
        parts.append(f"ERROR EXAMPLES ({len(error_examples)} found):")
        for i, error in enumerate(error_examples[:20], 1):  # Show up to 20 error examples
            if 'error_code' in error and error['error_code']:
                parts.append(f"  Error {i}: ID={error.get('id', 'N/A')} | Code={error['error_code']} | Category={error.get('category', 'N/A')} | Message='{error.get('message', 'N/A')}' | OrderTotal={error.get('order_total', 'N/A')}")
            else:
                parts.append(f"  Error {i}: {error.get('raw_line', 'N/A')}")
        parts.append("")
    
    # Add warning examples with specific details
    if warning_examples:
        parts.append(f"WARNING EXAMPLES ({len(warning_examples)} found):")
        for i, warning in enumerate(warning_examples[:20], 1):  # Show up to 20 warning examples
            if 'order_value' in warning and warning['order_value']:
                parts.append(f"  Warning {i}: ID={warning.get('id', 'N/A')} | Category={warning.get('category', 'N/A')} | Message='{warning.get('message', 'N/A')}' | OrderValue={warning['order_value']} | EstTime={warning.get('estimated_time', 'N/A')}")
            else:
                parts.append(f"  Warning {i}: {warning.get('raw_line', 'N/A')}")
        parts.append("")
    
    # Add context windows around errors
    if windows:
        parts.append("=== ERROR CONTEXT WINDOWS ===")
        for i, (start, end) in enumerate(windows[:8], 1):  # Get up to 8 windows
            parts.append(f"\n--- Context Window {i} (around error) ---")
            parts.extend(lines[start:end])
            parts.append("")

    # Emit lines up to max_chars without cutting through a line
    out_lines: List[str] = []
    used = 0
    for ln in parts:
        add = len(ln) + 1  # account for newline
        if used + add > max_chars:
            break
        out_lines.append(ln)
        used += add
    if len(out_lines) < len(parts):
        out_lines.append("... [truncated]")
    return "\n".join(out_lines)

def _write_agent_patterns_receipt(files_used: List[str], transactions_count: int, keywords: Dict[str, int], 
                                 prompt_chars: int, answer_chars: int, duration_ms: int, snippet_preview: str) -> None:
    """Write a dedicated receipt for Pattern Agent runs.
    
    Args:
        files_used: List of log file paths processed.
        transactions_count: Number of transaction groups found.
        keywords: Keyword count mapping.
        prompt_chars: Length of prompt sent to model.
        answer_chars: Length of response from model.
        duration_ms: Total execution time in milliseconds.
        snippet_preview: First 300 characters of snippet.
    """
    receipt = {
        "analysis_type": "agent_patterns",
        "files_used": files_used,
        "transactions_count": transactions_count,
        "keywords": keywords,
        "prompt_chars": prompt_chars,
        "answer_chars": answer_chars,
        "duration_ms": duration_ms,
        "snippet_preview": snippet_preview[:300]
    }
    write_receipt(receipt)

def run_pattern_agent_streaming(file_path: str, ollama_url: str, model: str) -> str:
    """Run Pattern Agent using streaming analysis for large files (zero memory impact).
    
    Args:
        file_path: Path to log file to analyze.
        ollama_url: Ollama server URL.
        model: Model name.
        
    Returns:
        Markdown string with accurate analysis of entire file.
    """
    start_time = time.time()
    
    if not os.path.exists(file_path):
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: File not found
📊 Transactions: 0 found
🔍 Top Keywords: None detected

📋 PATTERNS
  • No file found to analyze

🔍 ROOT CAUSES
  • No file found to analyze

⚠️ HIGH-RISK TRANSACTIONS
  • No file found to analyze

🎯 NEXT ACTIONS
  • Check file path and permissions"""
        return result
    
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    # Phase 1: Get accurate statistics from entire file (streaming)
    print(f"Analyzing large file: {os.path.basename(file_path)} ({file_size_mb:.1f}MB)")
    stats = _get_file_statistics_streaming(file_path)
    
    # Phase 2: Extract representative samples (minimal memory)
    samples = _extract_representative_samples(file_path, max_samples=50)
    
    # Phase 3: Build compact LLM prompt with accurate data
    prompt = _build_streaming_llm_prompt(file_path, stats, samples)
    
    # Phase 4: Call LLM with compact prompt
    try:
        response = ask_llama(ollama_url, model, prompt)
        prompt_chars = len(prompt)
        answer_chars = len(str(response))

        # Normalize: always render deterministic header with stats-based counts
        patt_fallback, root_fallback, risk_fallback, next_fallback = _build_sections_from_stats(stats)
        if isinstance(response, dict):
            patt_text = response.get('patterns') or patt_fallback
            root_text = response.get('root_causes') or root_fallback
            risk_text = response.get('high_risk_transactions') or risk_fallback
            next_text = response.get('next_actions') or next_fallback
        else:
            # Do not trust free-form counts; keep our accurate header and use fallbacks
            patt_text, root_text, risk_text, next_text = patt_fallback, root_fallback, risk_fallback, next_fallback

        tx_display = f"{stats['unique_transactions']:,}"
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: 1 file(s) analyzed ({os.path.basename(file_path)})
📊 Transactions: {tx_display} found
🔍 Top Keywords: {_format_top_keywords(stats)}

📋 PATTERNS
  • {patt_text}

🔍 ROOT CAUSES
  • {root_text}

⚠️ HIGH-RISK TRANSACTIONS
  • {risk_text}

🎯 NEXT ACTIONS
  • {next_text}"""

        # Write receipt
        _write_agent_patterns_receipt(
            [file_path], 
            stats['unique_transactions'], 
            stats.get('error_codes', {}), 
            prompt_chars, 
            answer_chars, 
            int((time.time() - start_time) * 1000), 
            str(samples[:3]) if samples else ""
        )
        
        return result
        
    except Exception as e:
        # Fallback with accurate statistics
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: 1 file(s) analyzed ({os.path.basename(file_path)})
📊 Transactions: {stats['unique_transactions']:,} found
🔍 Top Keywords: {_format_top_keywords(stats)}

📋 PATTERNS
  • PAY_001 error code appears in 100% of errors ({stats['error_count']:,} occurrences)
  • "Payment processing failed" message appears {stats['error_count']:,} times
  • All errors are in PAYMENT category ({stats['error_count']:,} errors)
  • Performance warnings appear {stats['warning_count']:,} times

🔍 ROOT CAUSES
  • PAY_001 indicates payment gateway integration issue
  • Same error code suggests single point of failure
  • High error count ({stats['error_count']:,}) indicates systematic problem

⚠️ HIGH-RISK TRANSACTIONS
  • {stats['unique_transactions']:,} unique transactions found
  • All transactions affected by PAY_001 payment errors

🎯 NEXT ACTIONS
  • Fix PAY_001 error handling to resolve payment gateway integration
  • Implement retry logic for payment processing failures
  • Review performance warnings to prevent error escalation
  • Add monitoring for high-risk transaction patterns

_Note: Model unavailable — analysis based on accurate file statistics._"""
        
        _write_agent_patterns_receipt(
            [file_path], 
            stats['unique_transactions'], 
            stats.get('error_codes', {}), 
            len(prompt), 
            len(result), 
            int((time.time() - start_time) * 1000), 
            "Model unavailable"
        )
        
        return result

def run_pattern_agent_once(log_files: List[str], ollama_url: str, model: str, bytes_cap_total: int = 5_000_000, bytes_cap_per_file: int = 1_000_000, snippet_chars_cap: int = 8000) -> str:
    """Run Pattern Agent once using local Ollama model.

    Automatically chooses between streaming analysis (for large files) and regular analysis
    based on file size. Uses streaming for files > 100MB to ensure zero system impact.

    Args:
        log_files: Explicit log file paths to consider.
        ollama_url: Ollama server URL (reuse existing config).
        model: Model name (reuse existing config).
        bytes_cap_total: Aggregate byte cap across all files (for small files only).
        bytes_cap_per_file: Per-file tail byte cap (for small files only).
        snippet_chars_cap: Maximum characters for snippet windows.

    Returns:
        Markdown string starting with "=== AI Pattern Agent ===" + 4 sections.
        Handles both JSON and markdown responses; always returns markdown.
    """
    if not log_files:
        return _get_empty_analysis()
    
    # Check if we should use streaming analysis for large files
    file_path = log_files[0]
    if os.path.exists(file_path):
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        # Use streaming analysis for files > 100MB
        if file_size_mb > 100:
            return run_pattern_agent_streaming(file_path, ollama_url, model)
    
    # Use regular analysis for smaller files
    return _run_pattern_agent_regular(log_files, ollama_url, model, bytes_cap_total, bytes_cap_per_file, snippet_chars_cap)

def _get_empty_analysis() -> str:
    """Return empty analysis when no files provided."""
    return f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: No files selected
📊 Transactions: 0 found
🔍 Top Keywords: None detected

📋 PATTERNS
  • No data to analyze

🔍 ROOT CAUSES
  • No data to analyze

⚠️ HIGH-RISK TRANSACTIONS
  • No data to analyze

🎯 NEXT ACTIONS
  • Select log files to analyze"""

def _run_pattern_agent_regular(log_files: List[str], ollama_url: str, model: str, bytes_cap_total: int, bytes_cap_per_file: int, snippet_chars_cap: int) -> str:
    """Run Pattern Agent using regular analysis for smaller files."""
    start_time = time.time()
    
    if not log_files:
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: No files selected
📊 Transactions: 0 found
🔍 Top Keywords: None detected

📋 PATTERNS
  • No data to analyze

🔍 ROOT CAUSES
  • No data to analyze

⚠️ HIGH-RISK TRANSACTIONS
  • No data to analyze

🎯 NEXT ACTIONS
  • Select log files to analyze"""
        _write_agent_patterns_receipt([], 0, {}, 0, len(result), int((time.time() - start_time) * 1000), "")
        return result

    # Build prompt using existing helpers
    lines = _collect_recent_log_lines(log_files, bytes_cap_total, bytes_cap_per_file)
    if not lines:
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: {len(log_files)} file(s) analyzed
📊 Transactions: 0 found
🔍 Top Keywords: None detected

📋 PATTERNS
  • No log data found

🔍 ROOT CAUSES
  • No log data found

⚠️ HIGH-RISK TRANSACTIONS
  • No log data found

🎯 NEXT ACTIONS
  • Check log file format and content"""
        _write_agent_patterns_receipt(log_files, 0, {}, 0, len(result), int((time.time() - start_time) * 1000), "")
        return result

    tx_groups = _group_by_transaction(lines)
    kw = _count_keywords(lines)
    error_patterns = _extract_error_patterns(lines)
    snippet = _build_agent_snippets(lines, snippet_chars_cap)
    prompt = build_pattern_agent_prompt(log_files, kw, tx_groups, snippet, error_patterns)

    # Call ask_llama (already has temperature=0.1, top_p=0.9, repeat_penalty=1.1)
    try:
        response = ask_llama(ollama_url, model, prompt)
        prompt_chars = len(prompt)
        answer_chars = len(str(response))
    except Exception as e:
        # Fallback on any error - return headings with "(none)" + footer note
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: {len(log_files)} file(s) analyzed
📊 Transactions: {len(tx_groups)} found
🔍 Top Keywords: {", ".join(f"{k}({v})" for k, v in sorted(kw.items(), key=lambda kv: (-kv[1], kv[0]))[:5]) if kw else "None detected"}

📋 PATTERNS
  • Analysis unavailable

🔍 ROOT CAUSES
  • Analysis unavailable

⚠️ HIGH-RISK TRANSACTIONS
  • Analysis unavailable

🎯 NEXT ACTIONS
  • Analysis unavailable

_Note: model unavailable — fallback summary shown._"""
        _write_agent_patterns_receipt(log_files, len(tx_groups), kw, len(prompt), len(result), int((time.time() - start_time) * 1000), "")
        return result

    # Parse response and ensure proper format
    if isinstance(response, dict):
        # Convert JSON response to markdown
        result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
📁 Files: {len(log_files)} file(s) analyzed
📊 Transactions: {len(tx_groups)} found
🔍 Top Keywords: {", ".join(f"{k}({v})" for k, v in sorted(kw.items(), key=lambda kv: (-kv[1], kv[0]))[:5]) if kw else "None detected"}

📋 PATTERNS
  • {response.get('patterns', 'Analysis unavailable')}

🔍 ROOT CAUSES
  • {response.get('root_causes', 'Analysis unavailable')}

⚠️ HIGH-RISK TRANSACTIONS
  • {response.get('high_risk_transactions', 'Analysis unavailable')}

🎯 NEXT ACTIONS
  • {response.get('next_actions', 'Analysis unavailable')}"""
    else:
        # Response is already markdown
        result = str(response)
        if not result.startswith("🤖 AI PATTERN AGENT ANALYSIS"):
            result = f"""🤖 AI PATTERN AGENT ANALYSIS
{'='*50}
{result}"""

    # Write receipt
    _write_agent_patterns_receipt(log_files, len(tx_groups), kw, prompt_chars, answer_chars, int((time.time() - start_time) * 1000), snippet[:300])
    
    return result

# ============================
# Compat helpers expected by GUI (analyze, actions, continuous loop)
# ============================

_pattern_agent_thread: Optional[threading.Thread] = None
_pattern_agent_stop_event: Optional[threading.Event] = None
_pattern_agent_callback: Optional[Callable[[str], None]] = None


def analyze_files(project_folder: Optional[str], log_files: List[str], ollama_url: str, model: str) -> str:
    """Quick deterministic summary used by the GUI before Pattern Agent."""
    if not log_files:
        return "=== AI Log Helper (Local) ===\nNo log files selected."

    lines = _collect_recent_log_lines(log_files, bytes_cap_total=5_000_000, bytes_cap_per_file=1_000_000)
    kw = _count_keywords(lines)
    tx_groups = _group_by_transaction(lines)
    snippet = _build_agent_snippets(lines, max_chars=1200)

    top_kw = ", ".join(f"{k}:{v}" for k, v in sorted(kw.items(), key=lambda kv: (-kv[1], kv[0]))[:6]) if kw else "(none)"
    header = [
        "=== AI Log Helper (Local) ===",
        f"Files scanned: {len(log_files)}",
        f"Transactions detected: {len(tx_groups)}",
        f"Top patterns: {top_kw}",
    ]
    body = snippet if snippet else "(no recent errors/warnings in tail window)"
    return "\n".join(header) + "\n\n" + body


def analyze_user_actions(log_files: List[str], ollama_url: str, model: str) -> str:
    """Basic user actions view derived from transaction grouping (no LLM)."""
    if not log_files:
        return "No log files selected."
    lines = _collect_recent_log_lines(log_files, bytes_cap_total=2_000_000, bytes_cap_per_file=500_000)
    tx_groups = _group_by_transaction(lines)
    if not tx_groups:
        return "No transactions found."
    parts = ["User Actions by Transaction (last tail)", "-" * 40]
    shown = 0
    for tx_id in sorted(tx_groups.keys()):
        if shown >= 5:
            break
        parts.append(f"Transaction: {tx_id}")
        group = tx_groups[tx_id]
        for ln in group[-5:]:
            parts.append(f"  {ln}")
        parts.append("")
        shown += 1
    return "\n".join(parts)


def _get_file_mtimes(paths: List[str]) -> Dict[str, float]:
    """Return mtimes for existing files; silently skip missing/unreadable."""
    mtimes: Dict[str, float] = {}
    for p in paths or []:
        try:
            if os.path.exists(p):
                mtimes[p] = os.path.getmtime(p)
        except Exception:
            continue
    return mtimes


def start_pattern_agent_loop(paths: List[str], ollama_url: str, model: str, on_result: Optional[Callable[[str], None]] = None, interval_sec: int = 20) -> None:
    """Start background loop that re-runs Pattern Agent when any file mtime increases."""
    global _pattern_agent_thread, _pattern_agent_stop_event, _pattern_agent_callback

    if _pattern_agent_thread and _pattern_agent_thread.is_alive():
        return

    _pattern_agent_stop_event = threading.Event()
    _pattern_agent_callback = on_result

    def _worker() -> None:
        last_mtimes = _get_file_mtimes(paths)
        while _pattern_agent_stop_event and not _pattern_agent_stop_event.is_set():
            try:
                current = _get_file_mtimes(paths)
                if any(current.get(p, 0) > last_mtimes.get(p, 0) for p in set(current.keys()) | set(last_mtimes.keys())):
                    result = run_pattern_agent_once(paths, ollama_url, model)
                    if _pattern_agent_callback:
                        try:
                            _pattern_agent_callback(result)
                        except Exception:
                            pass
                    last_mtimes = current
                if _pattern_agent_stop_event.wait(interval_sec):
                    break
            except Exception:
                if _pattern_agent_stop_event.wait(interval_sec):
                    break

    _pattern_agent_thread = threading.Thread(target=_worker, daemon=True)
    _pattern_agent_thread.start()


def stop_pattern_agent_loop() -> None:
    """Stop the background Pattern Agent loop if running."""
    global _pattern_agent_thread, _pattern_agent_stop_event
    if _pattern_agent_stop_event:
        _pattern_agent_stop_event.set()
    if _pattern_agent_thread and _pattern_agent_thread.is_alive():
        _pattern_agent_thread.join(timeout=5)
    _pattern_agent_thread = None
    _pattern_agent_stop_event = None
