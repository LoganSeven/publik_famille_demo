# documents/tests.py
"""
Test suite for the documents application.

This module validates access control for documents, ensuring
that users can only see their own documents in the list view.
"""

from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice
from documents.models import Document, DocumentKind


class DocumentsAccessTest(TestCase):
    """
    Test cases for document access control.

    Ensures that a user cannot see documents belonging
    to another user.
    """

    def setUp(self):
        """
        Prepare test fixtures.

        Creates:
        - Two users (u1, u2).
        - A child associated with u1.
        - An activity and enrollment for the child.
        - An invoice and associated document linked to u1.
        """
        self.u1 = User.objects.create_user("u1", password="u1")
        self.u2 = User.objects.create_user("u2", password="u2")

        child = Child.objects.create(
            parent=self.u1,
            first_name="A",
            last_name="B",
            birth_date="2016-01-01",
        )
        act = Activity.objects.create(title="Act", fee=10.0, is_active=True)
        enroll = Enrollment.objects.create(child=child, activity=act)
        inv = Invoice.objects.create(enrollment=enroll, amount=10.0)

        Document.objects.create(
            user=self.u1,
            kind=DocumentKind.FACTURE,
            title="Facture #1",
            file="invoices/x.pdf",
            invoice=inv,
        )

    def test_user_sees_only_own_documents(self):
        """
        Ensure that users cannot see documents belonging to others.

        Logs in as u2 and checks that the document created for u1
        does not appear in the list view.
        """
        self.client.login(username="u2", password="u2")
        resp = self.client.get(reverse("documents:list"))
        self.assertNotContains(resp, "Facture #1")
