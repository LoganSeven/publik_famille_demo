# accounts/views_identity.py
"""
Views for user identity verification.

This module implements simulation and production backends
for verifying a user's identity before allowing enrollment
in activities. The simulation mode marks the user profile
as verified locally, while the production mode integrates
with an external OIDC provider such as Authentic.
"""

import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from typing import Dict, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import UserProfile


def _conf(name: str, default=None):
    """
    Resolve configuration from settings or environment.

    Parameters
    ----------
    name : str
        The name of the configuration variable.
    default : any, optional
        A fallback value if not defined.

    Returns
    -------
    any
        The configuration value from settings, environment,
        or the provided default.
    """
    if hasattr(settings, name):
        return getattr(settings, name)
    return os.environ.get(name, default)


def _backend() -> str:
    """
    Return the active identity backend.

    Returns
    -------
    str
        The backend type. Defaults to ``simulation`` if undefined.
    """
    val = _conf("IDENTITY_BACKEND", "simulation")
    return val.strip().lower() if isinstance(val, str) else "simulation"


def _ok_next(request: HttpRequest, nxt: Optional[str]) -> str:
    """
    Sanitize a ``next`` URL parameter.

    Parameters
    ----------
    request : HttpRequest
        The current request.
    nxt : str, optional
        The requested redirection path.

    Returns
    -------
    str
        The safe URL path if allowed, or '/' as fallback.
    """
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return "/"


# Patterns corresponding to POST-only endpoints
_POST_ONLY_PATTERNS = [
    re.compile(r"^/activities/(?P<pk>\d+)/inscrire/?$"),
]


def _sanitize_resume_url(nxt: str) -> str:
    """
    Transform a POST-only endpoint into a safe GET endpoint.

    Parameters
    ----------
    nxt : str
        The requested ``next`` path.

    Returns
    -------
    str
        The sanitized path. If the input matches a POST-only
        pattern, the URL is converted to the corresponding
        activity detail view.
    """
    for pat in _POST_ONLY_PATTERNS:
        m = pat.match(nxt or "")
        if m:
            return f"/activities/{m.group('pk')}/"
    return nxt or "/"


def _http_post(
    url: str,
    data: Dict,
    auth: Optional[tuple] = None,
    timeout: int = 5,
) -> Dict:
    """
    Perform an HTTP POST request.

    Parameters
    ----------
    url : str
        The target URL.
    data : dict
        The form-encoded payload.
    auth : tuple, optional
        Optional (username, password) for basic authentication.
    timeout : int
        Timeout in seconds.

    Returns
    -------
    dict
        The parsed JSON response.
    """
    payload = urllib.parse.urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth:
        token = ("%s:%s" % auth).encode()
        import base64

        headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, headers: Optional[Dict] = None, timeout: int = 5) -> Dict:
    """
    Perform an HTTP GET request.

    Parameters
    ----------
    url : str
        The target URL.
    headers : dict, optional
        Optional HTTP headers.
    timeout : int
        Timeout in seconds.

    Returns
    -------
    dict
        The parsed JSON response.
    """
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@login_required
def verify_identity(request: HttpRequest) -> HttpResponse:
    """
    Entry point for identity verification.

    In simulation mode:
    - On POST, mark the profile as verified and redirect.
    - On GET, render the verification template.

    In production mode:
    - Redirect to ``verify_start`` to initiate OIDC flow.
    """
    nxt = request.GET.get("next") or "/"
    if _backend() == "simulation":
        if request.method == "POST":
            if not request.user.is_staff and not request.user.is_superuser:
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                if not profile.id_verified:
                    profile.id_verified = True
                    profile.save(update_fields=["id_verified"])
            messages.success(request, "Identité vérifiée.")
            safe_next = _sanitize_resume_url(
                _ok_next(request, request.POST.get("next") or nxt)
            )
            return redirect(safe_next)

        safe_next = _sanitize_resume_url(_ok_next(request, nxt))
        return render(
            request,
            "accounts/verify_identity.html",
            {"next": safe_next, "mode": "simulation"},
        )

    # Production (Authentic / OIDC): delegate to verify_start
    return verify_start(request)


@login_required
def verify_start(request: HttpRequest) -> HttpResponse:
    """
    Start the OIDC identity verification flow.

    Stores state in the session and redirects to the configured
    authorization endpoint.
    """
    nxt = _sanitize_resume_url(_ok_next(request, request.GET.get("next") or "/"))
    state = secrets.token_urlsafe(24)
    request.session["idv_state"] = state
    request.session["idv_next"] = nxt

    base = _conf("AUTHENTIC_AUTHORIZE_URL", "")
    client_id = _conf("AUTHENTIC_CLIENT_ID", "")
    redirect_uri = _conf(
        "AUTHENTIC_REDIRECT_URI", request.build_absolute_uri("/accounts/verify/callback/")
    )
    scope = _conf("AUTHENTIC_SCOPE", "openid profile")

    if not base or not client_id:
        return redirect("accounts_verify_identity" + f"?next={urllib.parse.quote(nxt)}")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"
    return redirect(url)


@login_required
def verify_callback(request: HttpRequest) -> HttpResponse:
    """
    Handle callback from OIDC provider after verification.

    Validates the state, exchanges authorization code for
    an access token, fetches user info, and updates the
    user profile as verified.

    Parameters
    ----------
    request : HttpRequest
        The callback request containing authorization data.

    Returns
    -------
    HttpResponse
        Redirection to the safe next page.
    """
    err = request.GET.get("error")
    code = request.GET.get("code")
    state = request.GET.get("state")
    exp_state = request.session.get("idv_state")
    nxt = request.session.get("idv_next", "/")

    if err:
        messages.error(request, "Échec de vérification.")
        return redirect(_sanitize_resume_url(_ok_next(request, nxt)))

    if not code or not state or not exp_state or state != exp_state:
        messages.error(request, "Session invalide.")
        return redirect(_sanitize_resume_url(_ok_next(request, nxt)))

    token_url = _conf("AUTHENTIC_TOKEN_URL", "")
    client_id = _conf("AUTHENTIC_CLIENT_ID", "")
    client_secret = _conf("AUTHENTIC_CLIENT_SECRET", "")
    redirect_uri = _conf(
        "AUTHENTIC_REDIRECT_URI", request.build_absolute_uri("/accounts/verify/callback/")
    )
    dry = _conf("AUTHENTIC_DRY_RUN", "0") in {"1", "true", "True"}

    success = False
    if dry:
        success = True
    elif token_url and client_id and client_secret:
        try:
            token = _http_post(
                token_url,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                auth=None,
                timeout=6,
            )
            access = token.get("access_token")
            if access:
                userinfo_url = _conf("AUTHENTIC_USERINFO_URL", "")
                if userinfo_url:
                    _ = _http_get(
                        userinfo_url,
                        headers={"Authorization": f"Bearer {access}"},
                        timeout=6,
                    )
                    success = True
        except Exception:
            success = False

    if success and not request.user.is_staff and not request.user.is_superuser:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if not profile.id_verified:
            profile.id_verified = True
            profile.save(update_fields=["id_verified"])
        messages.success(request, "Identité vérifiée.")
    else:
        messages.error(request, "Impossible de vérifier l'identité.")

    return redirect(_sanitize_resume_url(_ok_next(request, nxt)))
