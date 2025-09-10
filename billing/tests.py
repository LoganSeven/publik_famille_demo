# billing/tests.py
from django.test import TestCase
from django.contrib.auth.models import User
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice
from billing.pdf import generate_invoice_pdf
from billing.exceptions import PDFGenerationError
from pathlib import Path
from django.conf import settings

class BillingPdfTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('u', password='u')
        child = Child.objects.create(parent=self.u, first_name='C', last_name='D', birth_date='2014-01-01')
        act = Activity.objects.create(title='Act', fee=12.34, is_active=True)
        self.enroll = Enrollment.objects.create(child=child, activity=act)
        self.inv = Invoice.objects.create(enrollment=self.enroll, amount=12.34)

    def test_generate_invoice_pdf(self):
        target_dir = Path(settings.MEDIA_ROOT) / 'test_invoices'
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f'invoice_{self.inv.pk}.pdf'
        generate_invoice_pdf(self.inv, str(path))
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 100)  # un PDF non vide
