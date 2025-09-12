# activities/management/commands/bootstrap_demo.py
from calendar import monthrange
from datetime import date

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from activities.models import Activity
from families.models import Child


class Command(BaseCommand):
    help = "Create demo users and activities with dynamic rules."

    def handle(self, *args, **options):
        # Admin user
        admin, created = User.objects.get_or_create(
            username='admin',
            defaults={'is_staff': True, 'is_superuser': True},
        )
        if created:
            admin.set_password('admin123')
            admin.save()
            self.stdout.write(self.style.SUCCESS("Admin : admin/admin123"))

        # Default parent (unverified)
        parent, created = User.objects.get_or_create(
            username='parent',
            defaults={'email': 'parent@example.org'},
        )
        if created:
            parent.set_password('parent123')
            parent.save()
            self.stdout.write(self.style.SUCCESS("Parent : parent/parent123"))

        # Demo child
        Child.objects.get_or_create(
            parent=parent,
            first_name='Bob',
            last_name='Demo',
            birth_date='2015-06-01',
        )

        today = timezone.now().date()

        # Cantine: first day of next month to last day of that month
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        last_day = monthrange(next_month.year, next_month.month)[1]
        cantine_start = next_month
        cantine_end = date(next_month.year, next_month.month, last_day)
        Activity.objects.update_or_create(
            title='Cantine',
            defaults={
                'description': "Cantine scolaire du mois",
                'fee': 50.0,
                'capacity': 100,
                'start_date': cantine_start,
                'end_date': cantine_end,
                'is_active': True,
            },
        )

        # Séjour d'été: July 15 to August 15 (current year if in the future, else next year)
        current_year = today.year
        summer_start = date(current_year, 7, 15)
        summer_end = date(current_year, 8, 15)
        if today > summer_end:
            summer_start = date(current_year + 1, 7, 15)
            summer_end = date(current_year + 1, 8, 15)
        Activity.objects.update_or_create(
            title="Séjour d'été",
            defaults={
                'description': "Activité estivale",
                'fee': 150.0,          # corrected price
                'capacity': 100,       # default capacity
                'start_date': summer_start,
                'end_date': summer_end,
                'is_active': True,
            },
        )

        self.stdout.write(self.style.SUCCESS("Demo data initialized."))
