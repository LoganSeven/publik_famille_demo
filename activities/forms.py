# activities/forms.py
"""
Forms for the activities application.

This module defines form classes used to handle user input
related to enrollments in activities. Forms integrate with
Django's form system and provide validation and UI customization.
"""

from django import forms
from .models import Enrollment
from families.models import Child


class EnrollmentForm(forms.ModelForm):
    """
    Form for creating an enrollment in an activity.

    This form allows a parent to select one of their children
    and enroll them in an activity. The available children
    are restricted to those associated with the authenticated user.

    Attributes
    ----------
    child : forms.ModelChoiceField
        A dropdown field that lets the user choose one of their
        children for the enrollment. It uses a browser-default
        style for compatibility without requiring JavaScript.
    """

    # Child field restricted dynamically to the authenticated parent
    child = forms.ModelChoiceField(
        queryset=Child.objects.none(),
        label="Enfant",
        widget=forms.Select(attrs={"class": "browser-default"}),
    )

    class Meta:
        """
        Meta configuration for the EnrollmentForm.

        Attributes
        ----------
        model : Model
            The model associated with this form (Enrollment).
        fields : list
            The fields exposed to the form, here only 'child'.
        """

        model = Enrollment
        fields = ["child"]

    def __init__(self, *args, **kwargs):
        """
        Initialize the form with a user context.

        Parameters
        ----------
        *args : tuple
            Positional arguments passed to the base form.
        **kwargs : dict
            Keyword arguments. May include 'user' to filter
            the list of children.

        Notes
        -----
        If 'user' is authenticated, the child queryset is
        restricted to the children belonging to this user.
        Otherwise, the queryset is empty.
        """
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user and user.is_authenticated:
            self.fields["child"].queryset = Child.objects.filter(parent=user)
        else:
            self.fields["child"].queryset = Child.objects.none()
