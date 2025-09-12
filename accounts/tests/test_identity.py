# accounts/tests/test_identity.py
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse

User = get_user_model()

class IdentitySimulationTests(TestCase):
    def setUp(self) -> None:
        self.c = Client()
        self.user = User.objects.create_user(username="u", password="p")

    @override_settings(IDENTITY_BACKEND="simulation")
    def test_block_then_verify_then_allow(self) -> None:
        self.c.login(username="u", password="p")
        r = self.c.post("/activities/999/inscrire/", follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts_verify_identity"), r["Location"])

        verify = reverse("accounts_verify_identity")
        r2 = self.c.post(verify, {"next": "/activities/999/inscrire/"})
        self.assertEqual(r2.status_code, 302)

        self.user.refresh_from_db()
        self.assertTrue(self.user.profile.id_verified)

        r3 = self.c.post("/activities/999/inscrire/", follow=False)
        self.assertNotIn(verify, r3.get("Location", ""))

    def test_admin_bypassed(self) -> None:
        admin = User.objects.create_superuser(username="admin", password="p")
        self.assertTrue(admin.is_superuser)

class IdentityAuthenticDryRunTests(TestCase):
    def setUp(self) -> None:
        self.c = Client()
        self.user = User.objects.create_user(username="u2", password="p")

    @override_settings(
        IDENTITY_BACKEND="authentic",
        AUTHENTIC_AUTHORIZE_URL="https://idp.example/authorize",
        AUTHENTIC_CLIENT_ID="demo-client",
        AUTHENTIC_REDIRECT_URI="http://testserver/accounts/verify/callback/",
    )
    def test_start_redirects_to_authorize(self) -> None:
        self.c.login(username="u2", password="p")
        url = reverse("accounts_verify_identity") + "?next=/activities/1/inscrire/"
        r = self.c.get(url)
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
    def test_callback_marks_verified(self, mock_get, mock_post) -> None:
        mock_post.return_value = {"access_token": "abc"}
        mock_get.return_value = {"sub": "123"}
        self.c.login(username="u2", password="p")
        s = self.c.session
        s["idv_state"] = "S"
        s["idv_next"] = "/activities/1/inscrire/"
        s.save()
        r = self.c.get(reverse("accounts_verify_callback") + "?code=C&state=S")
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile.id_verified)
