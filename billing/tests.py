# billing/tests.py
"""
Test suite for the billing application.

This module provides unit tests for:
- PDF generation of invoices.
- Lingo gateway integration for invoice creation and payment.
"""

from django.test import TestCase
from django.contrib.auth.models import User
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice
from billing.pdf import generate_invoice_pdf
from billing.exceptions import PDFGenerationError  # noqa: F401  # Reserved for future tests
from pathlib import Path
from django.conf import settings
from unittest.mock import patch
from billing.gateways import LingoGateway


class BillingPdfTest(TestCase):
    """
    Test cases for invoice PDF generation.

    Ensures that invoice PDFs are created and stored correctly
    with sufficient size to confirm validity.
    """

    def setUp(self):
        """
        Prepare test fixtures.

        Creates a user, child, activity, enrollment, and invoice
        for use in PDF generation tests.
        """
        self.u = User.objects.create_user("u", password="u")
        child = Child.objects.create(
            parent=self.u,
            first_name="C",
            last_name="D",
            birth_date="2014-01-01",
        )
        act = Activity.objects.create(title="Act", fee=12.34, is_active=True)
        self.enroll = Enrollment.objects.create(child=child, activity=act)
        self.inv = Invoice.objects.create(enrollment=self.enroll, amount=12.34)

    def test_generate_invoice_pdf(self):
        """
        Validate PDF generation for invoices.

        Ensures that:
        - A PDF file is generated on disk.
        - The file is not empty (greater than 100 bytes).
        """
        target_dir = Path(settings.MEDIA_ROOT) / "test_invoices"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"invoice_{self.inv.pk}.pdf"

        generate_invoice_pdf(self.inv, str(path))

        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 100)


class LingoGatewayTest(TestCase):
    """
    Test cases for the Lingo gateway integration.

    Covers invoice creation and payment confirmation using
    mocked requests to the Lingo API.
    """

    def setUp(self):
        """
        Prepare test fixtures.

        Creates a user, child, activity, and enrollment for
        gateway integration tests.
        """
        u = User.objects.create_user("v", password="v")
        child = Child.objects.create(
            parent=u,
            first_name="A",
            last_name="B",
            birth_date="2014-01-01",
        )
        act = Activity.objects.create(title="A2", fee=10, is_active=True)
        self.enroll = Enrollment.objects.create(child=child, activity=act)

    def test_create_and_mark_paid(self):
        """
        Verify invoice creation and payment via the Lingo gateway.

        Steps:
        1. Create an invoice using the Lingo gateway.
        2. Mock a payment confirmation and update the invoice.
        3. Assert that the invoice status is set to PAID and
           that the paid_on field is populated.
        """
        gw = LingoGateway(base_url="http://l")

        # Step 1: Simulate invoice creation
        with patch("billing.gateways.requests.post") as post:
            post.return_value.json.return_value = {"id": "L1"}
            post.return_value.raise_for_status.return_value = None
            inv = gw.create_invoice(self.enroll, 10)
        self.assertEqual(inv.lingo_id, "L1")

        # Step 2: Simulate payment confirmation
        with patch("billing.gateways.requests.post") as post:
            post.return_value.json.return_value = {
                "status": Invoice.Status.PAID,
                "paid_on": "2024-01-02T03:04:05Z",
            }
            post.return_value.raise_for_status.return_value = None
            gw.mark_paid(inv)

        # Step 3: Verify updated invoice status
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)
        self.assertIsNotNone(inv.paid_on)
