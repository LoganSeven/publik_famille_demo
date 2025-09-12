# activities/management/commands/bootstrap_demo.py
"""
Management command to initialize demo data.

This command creates demo users, children, and activities
to populate the application with realistic default data.
It can be executed using::

    python manage.py bootstrap_demo
"""

from calendar import monthrange
from datetime import date

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from activities.models import Activity
from families.models import Child


class Command(BaseCommand):
    """
    Django management command for demo initialization.

    Creates:
    - An administrator account.
    - A default parent account (unverified).
    - A demo child for the parent.
    - Activities such as a canteen subscription and a summer camp.

    Attributes
    ----------
    help : str
        Short description displayed in ``python manage.py help``.
    """

    help = "Create demo users and activities with dynamic rules."

    def handle(self, *args, **options):
        """
        Execute the command.

        Parameters
        ----------
        *args : list
            Additional positional arguments.
        **options : dict
            Command options from the CLI.

        Notes
        -----
        - Admin credentials: ``admin/admin123``.
        - Parent credentials: ``parent/parent123``.
        - Activities are dynamically created based on current date.
        """
        # --- Create administrator account ---
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True},
        )
        if created:
            admin.set_password("admin123")
            admin.save()
            self.stdout.write(self.style.SUCCESS("Admin : admin/admin123"))

        # --- Create default parent account (unverified) ---
        parent, created = User.objects.get_or_create(
            username="parent",
            defaults={"email": "parent@example.org"},
        )
        if created:
            parent.set_password("parent123")
            parent.save()
            self.stdout.write(self.style.SUCCESS("Parent : parent/parent123"))

        # --- Create demo child ---
        Child.objects.get_or_create(
            parent=parent,
            first_name="Bob",
            last_name="Demo",
            birth_date="2015-06-01",
        )

        today = timezone.now().date()

        # --- Create canteen activity for the upcoming month ---
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        last_day = monthrange(next_month.year, next_month.month)[1]
        cantine_start = next_month
        cantine_end = date(next_month.year, next_month.month, last_day)
        Activity.objects.update_or_create(
            title="Cantine",
            defaults={
                "description": "Cantine scolaire du mois",
                "fee": 50.0,
                "capacity": 100,
                "start_date": cantine_start,
                "end_date": cantine_end,
                "is_active": True,
            },
        )

        # --- Create summer camp activity (adjust year if necessary) ---
        current_year = today.year
        summer_start = date(current_year, 7, 15)
        summer_end = date(current_year, 8, 15)
        if today > summer_end:
            summer_start = date(current_year + 1, 7, 15)
            summer_end = date(current_year + 1, 8, 15)
        Activity.objects.update_or_create(
            title="Séjour d'été",
            defaults={
                "description": "Activité estivale",
                "fee": 150.0,  # default price
                "capacity": 100,
                "start_date": summer_start,
                "end_date": summer_end,
                "is_active": True,
            },
        )

        self.stdout.write(self.style.SUCCESS("Demo data initialized."))
