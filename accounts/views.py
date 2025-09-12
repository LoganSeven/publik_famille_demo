# accounts/views.py
"""
Views for the accounts application.

This module defines user-related views such as sign-up
and logout. It integrates with Django's authentication
framework and custom forms.
"""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .forms import SignUpForm


def signup(request):
    """
    Handle user registration.

    Supports GET and POST methods:
    - GET: Render the sign-up form.
    - POST: Validate and create a new user account. If valid,
      the user is logged in immediately and redirected to home.

    Parameters
    ----------
    request : HttpRequest
        The incoming request object.

    Returns
    -------
    HttpResponse
        Rendered template with the form on GET or failed POST,
        or a redirect to home on successful sign-up.
    """
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
            )
            messages.success(request, "Account created. You are now logged in.")
            login(request, user)
            return redirect("home")
    else:
        form = SignUpForm()

    return render(request, "registration/signup.html", {"form": form})


@login_required
def logout_view(request):
    """
    Log out the current user.

    Terminates the user's session and redirects to the home page.

    Parameters
    ----------
    request : HttpRequest
        The incoming request object.

    Returns
    -------
    HttpResponseRedirect
        Redirect to the home page after logout.
    """
    logout(request)
    return redirect("home")
