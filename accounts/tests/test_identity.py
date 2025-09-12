# accounts/tests/test_identity.py
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

User = get_user_model()

class IdentitySimulationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="u1", password="pw")

    @override_settings(IDENTITY_BACKEND="simulation")
    def test_enroll_redirects_when_not_verified(self):
        self.client.login(username="u1", password="pw")
        r = self.client.post("/activities/999/inscrire/", follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts_verify_identity"), r["Location"])

    @override_settings(IDENTITY_BACKEND="simulation")
    def test_simulation_sets_flag_and_allows_post(self):
        self.client.login(username="u1", password="pw")
        r1 = self.client.post("/activities/999/inscrire/", follow=False)
        self.assertEqual(r1.status_code, 302)
        verify = reverse("accounts_verify_identity")
        self.assertTrue(r1["Location"].startswith(verify))
        r2 = self.client.post(verify, {"next": "/activities/999/inscrire/"})
        self.assertEqual(r2.status_code, 302)
        r3 = self.client.post("/activities/999/inscrire/", follow=False)
        self.assertNotIn(verify, r3.get("Location", ""))

class IdentityAuthenticTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="u2", password="pw")

    @override_settings(
        IDENTITY_BACKEND="authentic",
        AUTHENTIC_AUTHORIZE_URL="https://idp.example/authorize",
        AUTHENTIC_CLIENT_ID="demo-client",
        AUTHENTIC_REDIRECT_URI="http://testserver/accounts/verify/callback/",
    )
    def test_start_redirects_to_authorize(self):
        self.client.login(username="u2", password="pw")
        url = reverse("accounts_verify_identity") + "?next=/activities/1/inscrire/"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("https://idp.example/authorize?", r["Location"])
        self.assertIn("client_id=demo-client", r["Location"])

    @override_settings(
        IDENTITY_BACKEND="authentic",
        AUTHENTIC_AUTHORIZE_URL="https://idp.example/authorize",
        AUTHENTIC_CLIENT_ID="demo-client",
        AUTHENTIC_REDIRECT_URI="http://testserver/accounts/verify/callback/",
        AUTHENTIC_TOKEN_URL="https://idp.example/token",
        AUTHENTIC_USERINFO_URL="https://idp.example/userinfo",
    )
    @patch("accounts.views_identity._http_post")
    @patch("accounts.views_identity._http_get")
    def test_callback_marks_verified(self, mock_get, mock_post):
        mock_post.return_value = {"access_token": "abc"}
        mock_get.return_value = {"sub": "123"}
        self.client.login(username="u2", password="pw")
        s = self.client.session
        s["idv_state"] = "S"
        s["idv_next"] = "/activities/1/inscrire/"
        s.save()
        r = self.client.get(reverse("accounts_verify_callback") + "?code=C&state=S")
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile.id_verified)
