# accounts/forms.py
"""
Forms for the accounts application.

This module defines forms for user account management,
including sign-up with password confirmation.
"""

from django import forms
from django.contrib.auth.models import User


class SignUpForm(forms.ModelForm):
    """
    Form for registering a new user account.

    Extends Django's ``ModelForm`` to include password
    confirmation logic. Ensures that both password
    fields match before saving.

    Attributes
    ----------
    password : CharField
        The main password field with a password input widget.
    password_confirm : CharField
        A confirmation password field, also rendered as a
        password input.
    """

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "validate"}),
        label="Mot de passe",
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "validate"}),
        label="Confirmez le mot de passe",
    )

    class Meta:
        """
        Meta configuration for the SignUpForm.

        Attributes
        ----------
        model : Model
            The model bound to this form (Django's User model).
        fields : list
            The fields exposed to the form.
        labels : dict
            Custom labels for username and email fields.
        widgets : dict
            Widgets applied to username and email inputs.
        """

        model = User
        fields = ["username", "email", "password"]
        labels = {"username": "Nom d'utilisateur", "email": "E-mail"}
        widgets = {
            "username": forms.TextInput(attrs={"class": "validate"}),
            "email": forms.EmailInput(attrs={"class": "validate"}),
        }

    def clean(self):
        """
        Validate password confirmation.

        Ensures that the two password fields match. If they do not,
        an error is added to the ``password_confirm`` field.

        Returns
        -------
        dict
            The cleaned form data after validation.
        """
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        return cleaned
