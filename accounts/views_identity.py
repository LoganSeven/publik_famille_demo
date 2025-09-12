# accounts/views_identity.py
import json
import os
import secrets
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import UserProfile


def _conf(name: str, default=None):
    if hasattr(settings, name):
        return getattr(settings, name)
    return os.environ.get(name, default)


def _backend() -> str:
    return _conf("IDENTITY_BACKEND", "simulation").strip().lower()


def _ok_next(request, nxt: str) -> str:
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return "/"


def _http_post(url: str, data: dict, auth: tuple | None = None, timeout: int = 5) -> dict:
    payload = urllib.parse.urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth:
        token = ("%s:%s" % auth).encode()
        headers["Authorization"] = "Basic " + _b64(token)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, headers: dict | None = None, timeout: int = 5) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode("ascii")


@login_required
def verify_identity(request):
    nxt = request.GET.get("next") or "/"
    if _backend() == "simulation":
        if request.method == "POST":
            if not request.user.is_staff and not request.user.is_superuser:
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                if not profile.id_verified:
                    profile.id_verified = True
                    profile.save(update_fields=["id_verified"])
            messages.success(request, "Identité vérifiée.")
            return redirect(_ok_next(request, request.POST.get("next") or nxt))
        return render(request, "accounts/verify_identity.html", {"next": nxt, "mode": "simulation"})
    return redirect("accounts_verify_start" + f"?next={urllib.parse.quote(nxt)}")


@login_required
def verify_start(request):
    nxt = request.GET.get("next") or "/"
    state = secrets.token_urlsafe(24)
    request.session["idv_state"] = state
    base = _conf("AUTHENTIC_AUTHORIZE_URL", "")
    client_id = _conf("AUTHENTIC_CLIENT_ID", "")
    redirect_uri = _conf("AUTHENTIC_REDIRECT_URI", request.build_absolute_uri("/accounts/verify/callback/"))
    scope = _conf("AUTHENTIC_SCOPE", "openid profile")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if not base or not client_id:
        return redirect("accounts_verify_identity" + f"?next={urllib.parse.quote(nxt)}")
    url = f"{base}?{urllib.parse.urlencode(params)}"
    request.session["idv_next"] = _ok_next(request, nxt)
    return redirect(url)


@login_required
def verify_callback(request):
    err = request.GET.get("error")
    code = request.GET.get("code")
    state = request.GET.get("state")
    exp_state = request.session.get("idv_state")
    nxt = request.session.get("idv_next", "/")
    if err:
        messages.error(request, "Échec de vérification.")
        return redirect(_ok_next(request, nxt))
    if not code or not state or not exp_state or state != exp_state:
        messages.error(request, "Session invalide.")
        return redirect(_ok_next(request, nxt))
    token_url = _conf("AUTHENTIC_TOKEN_URL", "")
    client_id = _conf("AUTHENTIC_CLIENT_ID", "")
    client_secret = _conf("AUTHENTIC_CLIENT_SECRET", "")
    redirect_uri = _conf("AUTHENTIC_REDIRECT_URI", request.build_absolute_uri("/accounts/verify/callback/"))
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
                    _ = _http_get(userinfo_url, headers={"Authorization": f"Bearer {access}"}, timeout=6)
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
    return redirect(_ok_next(request, nxt))
