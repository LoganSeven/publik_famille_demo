# monitoring/html_logger.py
from pathlib import Path
from django.conf import settings
from django.utils.timezone import now

LOG_DIR = Path(settings.BASE_DIR) / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'app.log.html'

HEADER = '''<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Journaux</title>
<style>
.log-info{{ background:#e3f2fd; color:#0d47a1; padding:.5rem; border-left:4px solid #1976d2; margin:.25rem 0; }}
.log-warn{{ background:#fff8e1; color:#e65100; padding:.5rem; border-left:4px solid #ff9800; margin:.25rem 0; }}
.log-error{{ background:#ffebee; color:#b71c1c; padding:.5rem; border-left:4px solid #f44336; margin:.25rem 0; }}
.code{{ font-family:monospace; }}
</style></head><body>
<h3>Journaux applicatifs</h3>
'''

FOOTER = '</body></html>'

def _ensure_file():
    if not LOG_FILE.exists():
        LOG_FILE.write_text(HEADER, encoding='utf-8')

def _append(html_line: str):
    _ensure_file()
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(html_line + "\n")

def info(message: str):
    ts = now().strftime('%Y-%m-%d %H:%M:%S')
    _append(f'<div class="log-info"><strong>[INFO {ts}]</strong> {message}</div>')

def warn(message: str):
    ts = now().strftime('%Y-%m-%d %H:%M:%S')
    _append(f'<div class="log-warn"><strong>[WARN {ts}]</strong> {message}</div>')

def error(message: str):
    ts = now().strftime('%Y-%m-%d %H:%M:%S')
    _append(f'<div class="log-error"><strong>[ERROR {ts}]</strong> {message}</div>')
