# activities/models.py
"""
Database models for the activities application.

This module defines the Activity and Enrollment models.
Activities represent events or services offered to families,
while enrollments represent a child's participation in a
specific activity.
"""

from django.db import models
from django.utils import timezone
from families.models import Child


class Activity(models.Model):
    """
    Model representing an activity.

    Attributes
    ----------
    title : CharField
        The title of the activity (French verbose name: 'Titre').
    description : TextField
        Optional description of the activity (French verbose name: 'Description').
    fee : DecimalField
        Fee for the activity in euros (French verbose name: 'Tarif (€)').
    start_date : DateField
        Optional start date of the activity (French verbose name: 'Date de début').
    end_date : DateField
        Optional end date of the activity (French verbose name: 'Date de fin').
    capacity : PositiveIntegerField
        Optional maximum number of participants (French verbose name: 'Capacité').
    is_active : BooleanField
        Indicates whether the activity is active (French verbose name: 'Active').
    """

    title = models.CharField("Titre", max_length=200)
    description = models.TextField("Description", blank=True)
    fee = models.DecimalField(
        "Tarif (€)", max_digits=8, decimal_places=2, default=0
    )
    start_date = models.DateField("Date de début", null=True, blank=True)
    end_date = models.DateField("Date de fin", null=True, blank=True)
    capacity = models.PositiveIntegerField("Capacité", null=True, blank=True)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        """
        Metadata options for the Activity model.

        Attributes
        ----------
        ordering : list
            Default ordering of activities by title.
        """

        ordering = ["title"]

    def __str__(self) -> str:
        """
        Return a string representation of the activity.

        Returns
        -------
        str
            The activity title.
        """
        return self.title


class Enrollment(models.Model):
    """
    Model representing an enrollment of a child in an activity.

    Attributes
    ----------
    child : ForeignKey
        The child enrolled (related to families.Child).
    activity : ForeignKey
        The activity in which the child is enrolled.
    status : CharField
        Current status of the enrollment. Choices defined in the Status inner class.
    requested_on : DateTimeField
        Timestamp when the enrollment was requested.
    approved_on : DateTimeField
        Optional timestamp when the enrollment was approved.
    wcs_id : CharField
        Optional identifier used by a remote WCS backend.
    """

    class Status(models.TextChoices):
        """
        Enumeration of enrollment statuses.

        PENDING_PAYMENT
            Enrollment has been created but payment is pending.
        CONFIRMED
            Enrollment has been confirmed.
        CANCELLED
            Enrollment has been cancelled.
        """

        PENDING_PAYMENT = "PENDING_PAYMENT", "En attente de paiement"
        CONFIRMED = "CONFIRMED", "Confirmée"
        CANCELLED = "CANCELLED", "Annulée"

    child = models.ForeignKey(
        Child,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Enfant",
    )
    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Activité",
    )
    status = models.CharField(
        "Statut",
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
    )
    requested_on = models.DateTimeField("Demandée le", default=timezone.now)
    approved_on = models.DateTimeField("Approuvée le", null=True, blank=True)
    wcs_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        """
        Metadata options for the Enrollment model.

        Attributes
        ----------
        unique_together : tuple
            Prevents duplicate enrollment of the same child in the same activity.
        ordering : list
            Default ordering of enrollments by descending request date.
        """

        unique_together = ("child", "activity")
        ordering = ["-requested_on"]

    def __str__(self) -> str:
        """
        Return a string representation of the enrollment.

        Returns
        -------
        str
            A formatted string with child, activity, and status.
        """
        return f"{self.child} -> {self.activity} ({self.status})"
