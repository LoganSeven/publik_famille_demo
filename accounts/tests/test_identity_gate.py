from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from activities.models import Activity
from families.models import Child


class IdentityGateTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user(username="p", password="x")
        # Profil non vérifié par défaut
        UserProfile.objects.get_or_create(user=self.parent)
        self.child = Child.objects.create(
            parent=self.parent,
            first_name="Demo",
            last_name="Child",
            birth_date="2014-05-01",
        )
        self.activity = Activity.objects.create(
            title="Test",
            description="",
            capacity=10,
            fee=5.0,
            start_date=date.today() + timedelta(days=3),
            end_date=date.today() + timedelta(days=30),
            is_active=True,
        )
        self.client = Client()

    def test_enrollment_blocked_when_not_verified(self):
        self.client.login(username="p", password="x")
        url = reverse("activities:enroll", args=[self.activity.pk])
        resp = self.client.post(url, {"child": self.child.pk}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/verify/", resp["Location"])

    def test_enrollment_allowed_after_verification(self):
        profile = self.parent.profile
        profile.id_verified = True
        profile.save()

        self.client.login(username="p", password="x")
        url = reverse("activities:enroll", args=[self.activity.pk])
        resp = self.client.post(url, {"child": self.child.pk}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("activities:enrollments"), resp["Location"])
