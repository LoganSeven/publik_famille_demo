# families/forms.py
"""
Forms for the families application.

This module defines form classes for managing children
within the families app.
"""

from django import forms
from .models import Child


class ChildForm(forms.ModelForm):
    """
    Form for creating or updating a Child instance.

    Provides form fields for first name, last name,
    and birth date, with labels and widgets adapted
    for user-friendly rendering.
    """

    class Meta:
        """
        Meta configuration for ChildForm.

        Attributes
        ----------
        model : Model
            The model associated with this form (Child).
        fields : list
            The list of model fields included in the form.
        labels : dict
            Custom labels for form fields, translated for UI.
        widgets : dict
            Widget configurations for customizing field rendering.
        """

        model = Child
        fields = ["first_name", "last_name", "birth_date"]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
            "birth_date": "Date de naissance",
        }
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "validate"}),
            "last_name": forms.TextInput(attrs={"class": "validate"}),
            "birth_date": forms.DateInput(
                attrs={"type": "date", "class": "validate"}
            ),
        }
