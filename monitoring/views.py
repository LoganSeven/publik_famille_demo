# monitoring/views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from pathlib import Path
from django.conf import settings

@staff_member_required
def logs_view(request):
    log_file = Path(settings.BASE_DIR) / 'logs' / 'app.log.html'
    if log_file.exists():
        with log_file.open('r', encoding='utf-8') as f:
            html = f.read()
    else:
        html = "<p>Aucun log pour le moment.</p>"
    return render(request, 'monitoring/logs.html', {'log_html': html})
