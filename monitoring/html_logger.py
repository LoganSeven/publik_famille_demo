# monitoring/html_logger.py
"""
HTML logger for the monitoring application.

This module provides simple logging functions (info, warn, error)
that append log entries to an HTML file. The generated log file
can be displayed directly in a browser and styled with basic CSS.
"""

from pathlib import Path
from django.conf import settings
from django.utils.timezone import now

# ---------------------------------------------------------------------------
# Log file setup
# ---------------------------------------------------------------------------
LOG_DIR = Path(settings.BASE_DIR) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log.html"

# HTML header and footer for the log file
HEADER = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Journaux</title>
<style>
.log-info{ background:#e3f2fd; color:#0d47a1; padding:.5rem; border-left:4px solid #1976d2; margin:.25rem 0; }
.log-warn{ background:#fff8e1; color:#e65100; padding:.5rem; border-left:4px solid #ff9800; margin:.25rem 0; }
.log-error{ background:#ffebee; color:#b71c1c; padding:.5rem; border-left:4px solid #f44336; margin:.25rem 0; }
.code{ font-family:monospace; }
</style></head><body>
<h3>Journaux applicatifs</h3>
"""
FOOTER = "</body></html>"


def _ensure_file():
    """
    Ensure that the log file exists.

    If the file does not exist, it is created with the HTML header.
    """
    if not LOG_FILE.exists():
        LOG_FILE.write_text(HEADER, encoding="utf-8")


def _append(html_line: str):
    """
    Append a single HTML line to the log file.

    Parameters
    ----------
    html_line : str
        The HTML-formatted log entry to append.
    """
    _ensure_file()
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(html_line + "\n")


def info(message: str):
    """
    Log an informational message.

    Parameters
    ----------
    message : str
        The message to log.
    """
    ts = now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f'<div class="log-info"><strong>[INFO {ts}]</strong> {message}</div>')


def warn(message: str):
    """
    Log a warning message.

    Parameters
    ----------
    message : str
        The message to log.
    """
    ts = now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f'<div class="log-warn"><strong>[WARN {ts}]</strong> {message}</div>')


def error(message: str):
    """
    Log an error message.

    Parameters
    ----------
    message : str
        The message to log.
    """
    ts = now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f'<div class="log-error"><strong>[ERROR {ts}]</strong> {message}</div>')
