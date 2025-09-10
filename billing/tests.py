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
from unittest.mock import patch
from billing.gateways import LingoGateway

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
        self.assertGreater(path.stat().st_size, 100)

class LingoGatewayTest(TestCase):
    def setUp(self):
        u = User.objects.create_user('v', password='v')
        child = Child.objects.create(parent=u, first_name='A', last_name='B', birth_date='2014-01-01')
        act = Activity.objects.create(title='A2', fee=10, is_active=True)
        self.enroll = Enrollment.objects.create(child=child, activity=act)

    def test_create_and_mark_paid(self):
        gw = LingoGateway(base_url='http://l')
        with patch('billing.gateways.requests.post') as post:
            post.return_value.json.return_value = {'id': 'L1'}
            post.return_value.raise_for_status.return_value = None
            inv = gw.create_invoice(self.enroll, 10)
        self.assertEqual(inv.lingo_id, 'L1')
        with patch('billing.gateways.requests.post') as post:
            post.return_value.json.return_value = {'status': Invoice.Status.PAID, 'paid_on': '2024-01-02T03:04:05Z'}
            post.return_value.raise_for_status.return_value = None
            gw.mark_paid(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)
        self.assertIsNotNone(inv.paid_on)
