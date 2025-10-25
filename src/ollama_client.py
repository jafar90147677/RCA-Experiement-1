"""Ollama client module for AI Log Helper.

This module handles communication with the Ollama server for AI analysis.
"""
import json
import re
from typing import Union, Dict, Any

try:
    import requests
except ImportError:
    print("Error: requests library not found. Please install it with: pip install requests")
    raise

def _try_generate(ollama_url: str, model: str, prompt: str, timeout: int = 60) -> str:
    """Try to generate response using /api/generate endpoint."""
    url = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return (data.get("response") or "").strip()

def _try_chat(ollama_url: str, model: str, prompt: str, timeout: int = 60) -> str:
    """Try to generate response using /api/chat endpoint."""
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload = {"model": model, "messages": [{"role":"user","content": prompt}], "stream": False}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message") or {}
    return (msg.get("content") or "").strip()

def _parse_json(text: str) -> Union[Dict[str, Any], str]:
    """Parse JSON response or return text as-is."""
    # First try to parse as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from the text
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # If no JSON found, return the markdown text as-is
    # This handles the new markdown format
    return text

def ask_llama(ollama_url: str, model: str, prompt: str) -> Union[Dict[str, Any], str]:
    """
    Ask Llama model for analysis.

    Args:
        ollama_url: URL of the Ollama server
        model: Model name to use
        prompt: Prompt to send to the model

    Returns:
        Analysis result as dict or string
    """
    try:
        # Test basic connection first
        test_url = f"{ollama_url.rstrip('/')}/api/tags"
        try:
            response = requests.get(test_url, timeout=10)
            if response.status_code != 200:
                raise requests.RequestException(f"Server returned status {response.status_code}")
        except requests.RequestException as conn_e:
            # Log the connection error and re-raise
            print(f"Ollama connection failed: {conn_e}")
            raise

        try:
            text = _try_generate(ollama_url, model, prompt)
        except requests.HTTPError as e:
            # If /api/generate isn't there, fall back to /api/chat
            if e.response is not None and e.response.status_code == 404:
                text = _try_chat(ollama_url, model, prompt)
            else:
                raise
        except (requests.RequestException, requests.ConnectionError) as e:
            # Log the error and re-raise
            print(f"Request failed: {e}")
            raise
        return _parse_json(text)
    except (requests.RequestException, requests.ConnectionError) as e:
        return {
            "root_cause": f"Ollama server connection failed. Cannot reach the local AI model server at {ollama_url}.",
            "why": [
                f"Connection error: {e}",
                "Ollama server may not be running or accessible",
                "Network connectivity issues or incorrect server configuration"
            ],
            "next_steps": [
                "Start Ollama server by running: ollama serve",
                f"Ensure the {model} model is available by running: ollama pull {model}",
                "Verify the server URL and port configuration in the application"
            ]
        }
