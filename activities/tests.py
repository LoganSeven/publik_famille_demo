# activities/tests.py
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice
from django.test import override_settings
from unittest.mock import patch
from billing.gateways import get_billing_gateway

class FluxInscriptionPaiementTest(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user(username='p', password='p')
        self.child = Child.objects.create(parent=self.parent, first_name='A', last_name='B', birth_date='2016-01-01')
        self.activity = Activity.objects.create(title='Act', fee=10.00, is_active=True)

    def test_enroll_and_pay_generates_pdf_and_document(self):
        self.client.login(username='p', password='p')
        enroll = Enrollment.objects.create(child=self.child, activity=self.activity)
        inv = Invoice.objects.create(enrollment=enroll, amount=self.activity.fee)
        url = reverse('billing:pay_invoice', args=[inv.pk])
        resp = self.client.post(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        enroll.refresh_from_db()
        self.assertEqual(enroll.status, Enrollment.Status.CONFIRMED)
        self.assertTrue(hasattr(inv, 'document'))

    def test_pay_requires_post_and_csrf(self):
        self.client.login(username='p', password='p')
        enroll = Enrollment.objects.create(child=self.child, activity=self.activity)
        inv = Invoice.objects.create(enrollment=enroll, amount=self.activity.fee)
        url = reverse('billing:pay_invoice', args=[inv.pk])
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 405)
        client_csrf = Client(enforce_csrf_checks=True)
        client_csrf.login(username='p', password='p')
        resp_403 = client_csrf.post(url, {})
        self.assertEqual(resp_403.status_code, 403)

    def test_user_cannot_pay_others_invoice(self):
        other = User.objects.create_user(username='q', password='q')
        other_child = Child.objects.create(parent=other, first_name='X', last_name='Y', birth_date='2015-01-01')
        enroll = Enrollment.objects.create(child=other_child, activity=self.activity)
        inv = Invoice.objects.create(enrollment=enroll, amount=self.activity.fee)
        self.client.login(username='p', password='p')
        url = reverse('billing:pay_invoice', args=[inv.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        enroll.refresh_from_db()
        self.assertEqual(enroll.status, Enrollment.Status.PENDING_PAYMENT)

class GatewayModesTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user(username='pp', password='pp')
        self.child = Child.objects.create(parent=self.parent, first_name='X', last_name='Y', birth_date='2016-01-01')
        self.activity = Activity.objects.create(title='Act2', fee=12.34, is_active=True)

    @override_settings(BILLING_BACKEND='lingo', ENROLLMENT_BACKEND='wcs', BILLING_LINGO_BASE_URL='http://l')
    def test_gateways_simulated_modes(self):
        self.client.login(username='pp', password='pp')
        with patch('billing.gateways.requests.post') as post:
            post.return_value.json.return_value = {'id': 'L1'}
            post.return_value.raise_for_status.return_value = None
            resp = self.client.post(f"/activities/{self.activity.pk}/inscrire/", {'child': self.child.pk}, follow=True)
        self.assertEqual(resp.status_code, 200)
        enroll = Enrollment.objects.get(child=self.child, activity=self.activity)
        inv = Invoice.objects.get(enrollment=enroll)
        with patch('billing.gateways.requests.post') as post:
            post.return_value.json.return_value = {'status': Invoice.Status.PAID, 'paid_on': '2024-01-02T03:04:05Z'}
            post.return_value.raise_for_status.return_value = None
            pay = self.client.post(f"/billing/payer/{inv.pk}/", follow=True)
        self.assertEqual(pay.status_code, 200)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.PAID)

    @override_settings(BILLING_BACKEND='lingo', BILLING_LINGO_BASE_URL=None)
    def test_lingo_missing_base_url_raises(self):
        gw = get_billing_gateway()
        with self.assertRaises(Exception):
            gw.create_invoice(Enrollment.objects.create(child=self.child, activity=self.activity), 10)
