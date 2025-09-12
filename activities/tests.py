# activities/tests.py
"""
Test suite for the activities application.

This module contains integration and unit tests covering:
- Enrollment flows combined with billing (PDF/document generation, CSRF behavior).
- Gateway modes combining WCS for enrollments and Lingo for billing.
"""

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice
from unittest.mock import patch
from billing.gateways import get_billing_gateway


class FluxInscriptionPaiementTest(TestCase):
    """
    Test cases for the local enrollment and billing integration.

    Covers creation of enrollments, generation of invoices,
    payment flow validation, and security restrictions.
    """

    def setUp(self):
        """
        Prepare test fixtures.

        Creates a parent user, an associated child,
        and a sample activity used for enrollment.
        """
        self.parent = User.objects.create_user(username="p", password="p")
        self.child = Child.objects.create(
            parent=self.parent,
            first_name="A",
            last_name="B",
            birth_date="2016-01-01",
        )
        self.activity = Activity.objects.create(
            title="Act",
            fee=10.00,
            is_active=True,
        )

    def test_enroll_and_pay_generates_pdf_and_document(self):
        """
        Verify that enrollment and payment generate expected outputs.

        Ensures that after creating an enrollment and invoice,
        the payment endpoint confirms the enrollment and attaches
        a generated document to the invoice.
        """
        self.client.login(username="p", password="p")
        enroll = Enrollment.objects.create(child=self.child, activity=self.activity)
        inv = Invoice.objects.create(enrollment=enroll, amount=self.activity.fee)
        url = reverse("billing:pay_invoice", args=[inv.pk])
        resp = self.client.post(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        enroll.refresh_from_db()
        self.assertEqual(enroll.status, Enrollment.Status.CONFIRMED)
        self.assertTrue(hasattr(inv, "document"))

    def test_pay_requires_post_and_csrf(self):
        """
        Validate HTTP method and CSRF enforcement for payment.

        Ensures GET requests are rejected with 405
        and POST without CSRF token returns 403.
        """
        self.client.login(username="p", password="p")
        enroll = Enrollment.objects.create(child=self.child, activity=self.activity)
        inv = Invoice.objects.create(enrollment=enroll, amount=self.activity.fee)
        url = reverse("billing:pay_invoice", args=[inv.pk])

        # GET is not allowed
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 405)

        # POST without CSRF token is rejected
        client_csrf = Client(enforce_csrf_checks=True)
        client_csrf.login(username="p", password="p")
        resp_403 = client_csrf.post(url, {})
        self.assertEqual(resp_403.status_code, 403)

    def test_user_cannot_pay_others_invoice(self):
        """
        Ensure users cannot pay invoices belonging to others.

        Attempts to pay another parent's invoice should be blocked,
        and enrollment status must remain unchanged.
        """
        other = User.objects.create_user(username="q", password="q")
        # Using create directly on Child to simulate another parent
        other_child = Child.objects.create(
            parent=other,
            first_name="X",
            last_name="Y",
            birth_date="2015-01-01",
        )
        enroll = Enrollment.objects.create(child=other_child, activity=self.activity)

        self.client.login(username="p", password="p")
        url = reverse("billing:pay_invoice", args=[enroll.invoice.pk])  # type: ignore[attr-defined]
        resp = self.client.post(url)

        self.assertEqual(resp.status_code, 302)
        enroll.refresh_from_db()
        self.assertEqual(enroll.status, Enrollment.Status.PENDING_PAYMENT)


class GatewayModesTests(TestCase):
    """
    Test cases for gateway-based enrollment and billing integration.

    Covers enrollment creation via WCS and billing/payment flow via Lingo,
    as well as validation of error handling in case of misconfiguration.
    """

    def setUp(self):
        """
        Prepare test fixtures.

        Creates a parent user, an associated child,
        and an activity used for WCS/Lingo integration tests.
        """
        self.parent = User.objects.create_user(username="pp", password="pp")
        self.child = Child.objects.create(
            parent=self.parent,
            first_name="X",
            last_name="Y",
            birth_date="2016-01-01",
        )
        self.activity = Activity.objects.create(
            title="Act2",
            fee=12.34,
            is_active=True,
        )

    @override_settings(
        BILLING_BACKEND="lingo",
        ENROLLMENT_BACKEND="wcs",
        WCS_BASE_URL="http://wcs",
        WCS_API_TOKEN="t",
        BILLING_LINGO_BASE_URL="http://l",
    )
    def test_enroll_via_wcs_then_pay_via_lingo(self):
        """
        Verify full flow using WCS for enrollment and Lingo for billing.

        Ensures that:
        - Enrollment is created through the WCS gateway.
        - Invoice is generated and can be paid through the Lingo gateway.
        - Enrollment status is confirmed after payment.
        """
        self.client.login(username="pp", password="pp")

        # Simulate WCS enrollment creation
        with patch("activities.gateways.requests.post") as wcs_post:
            wcs_post.return_value.json.return_value = {"id": "W1"}
            wcs_post.return_value.raise_for_status.return_value = None
            resp = self.client.post(
                f"/activities/{self.activity.pk}/inscrire/",
                {"child": self.child.pk},
                follow=True,
            )
        self.assertEqual(resp.status_code, 200)

        enroll = Enrollment.objects.get(child=self.child, activity=self.activity)

        # Simulate Lingo payment
        with patch("billing.gateways.requests.post") as lingo_post:
            lingo_post.return_value.json.return_value = {
                "status": Invoice.Status.PAID,
                "paid_on": "2024-01-02T03:04:05Z",
            }
            lingo_post.return_value.raise_for_status.return_value = None
            pay = self.client.post(
                f"/billing/payer/{enroll.invoice.pk}/", follow=True  # type: ignore[attr-defined]
            )
        self.assertEqual(pay.status_code, 200)
        enroll.refresh_from_db()
        self.assertEqual(enroll.status, Enrollment.Status.CONFIRMED)

    @override_settings(ENROLLMENT_BACKEND="wcs", WCS_BASE_URL=None)
    def test_wcs_missing_base_url_raises(self):
        """
        Verify error handling when WCS base URL is missing.

        Directly instantiates the WCS enrollment gateway and ensures
        that a missing base URL raises an exception during enrollment creation.
        """
        from activities.gateways import get_enrollment_gateway

        gw = get_enrollment_gateway()
        with self.assertRaises(Exception):
            gw.create_enrollment(
                activity=Activity.objects.create(
                    title="Tmp", fee=1, is_active=True
                ),
                child=self.child,
            )
