# monitoring/views.py
"""
Views for the monitoring application.

This module provides administrative views for inspecting
application logs directly through the Django interface.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from pathlib import Path
from django.conf import settings


@staff_member_required
def logs_view(request):
    """
    Display application logs as HTML content.

    Restricted to staff members only. Attempts to read the
    log file ``logs/app.log.html`` relative to BASE_DIR and
    render its content in the monitoring template.

    Parameters
    ----------
    request : HttpRequest
        The current HTTP request.

    Returns
    -------
    HttpResponse
        A rendered template containing the log HTML or a
        placeholder message if no log file is available.
    """
    log_file = Path(settings.BASE_DIR) / "logs" / "app.log.html"

    # Read the log file if available, otherwise fallback with a placeholder
    if log_file.exists():
        with log_file.open("r", encoding="utf-8") as f:
            html = f.read()
    else:
        html = "<p>Aucun log pour le moment.</p>"

    return render(request, "monitoring/logs.html", {"log_html": html})
