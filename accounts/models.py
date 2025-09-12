# accounts/models.py
"""
Database models for the accounts application.

This module defines the UserProfile model, which extends
the built-in Django user with additional fields such as
identity verification status.
"""

from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """
    Profile model linked to the Django user.

    This model stores additional information about a user
    that is not present in the default Django user model.
    In particular, it includes the identity verification flag.

    Attributes
    ----------
    user : OneToOneField
        A one-to-one relationship with the user model defined
        by ``settings.AUTH_USER_MODEL``. Each user has exactly
        one profile.
    id_verified : BooleanField
        Indicates whether the user's identity has been verified.
        Defaults to False.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    id_verified = models.BooleanField(default=False)

    def __str__(self) -> str:
        """
        Return a string representation of the user profile.

        Returns
        -------
        str
            A formatted string showing the associated user
            followed by the word "profile".
        """
        return f"{self.user} profile"
