from calendar import monthrange
from datetime import date

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from activities.models import Activity


class BootstrapDemoTests(TestCase):
    def test_dynamic_activity_creation(self):
        call_command("bootstrap_demo")

        today = timezone.now().date()

        # Cantine : 1er → dernier jour du mois suivant, cap=100, fee=50
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        last_day = monthrange(next_month.year, next_month.month)[1]
        cantine_start = next_month
        cantine_end = date(next_month.year, next_month.month, last_day)
        cantine = Activity.objects.get(title="Cantine")
        assert cantine.start_date == cantine_start
        assert cantine.end_date == cantine_end
        assert cantine.capacity == 100
        assert float(cantine.fee) == 50.0

        # Séjour d'été : 15/07 → 15/08, cap=100, fee=150
        summer = Activity.objects.get(title="Séjour d'été")
        summer_start = date(today.year, 7, 15)
        summer_end = date(today.year, 8, 15)
        if today > summer_end:
            summer_start = date(today.year + 1, 7, 15)
            summer_end = date(today.year + 1, 8, 15)
        assert summer.start_date == summer_start
        assert summer.end_date == summer_end
        assert summer.capacity == 100
        assert float(summer.fee) == 150.0
