# families/models.py
"""
Database models for the families application.

This module defines the Child model, which represents
a child belonging to a parent user.
"""

from django.conf import settings
from django.db import models


class Child(models.Model):
    """
    Model representing a child in a family.

    Attributes
    ----------
    parent : ForeignKey
        The user (parent) associated with this child.
    first_name : CharField
        The child's first name (verbose name: 'Prénom').
    last_name : CharField
        The child's last name (verbose name: 'Nom').
    birth_date : DateField
        The child's date of birth (verbose name: 'Date de naissance').
    """

    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="children",
    )
    first_name = models.CharField("Prénom", max_length=100)
    last_name = models.CharField("Nom", max_length=100)
    birth_date = models.DateField("Date de naissance")

    class Meta:
        """
        Metadata for the Child model.

        Attributes
        ----------
        ordering : list
            Default ordering by last name, then first name.
        """

        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        """
        Return a string representation of the child.

        Returns
        -------
        str
            Full name of the child (first name + last name).
        """
        return f"{self.first_name} {self.last_name}"

    @classmethod
    def create(cls, **kwargs):
        """
        Proxy method to create a Child instance.

        This method exists primarily for use in tests, to allow
        ``Child.create(...)`` calls as a shorthand for
        ``Child.objects.create(...)``.

        Parameters
        ----------
        **kwargs : dict
            Keyword arguments passed to the model manager.

        Returns
        -------
        Child
            The created Child instance.
        """
        return cls.objects.create(**kwargs)
