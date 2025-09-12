# accounts/tests/test_identity.py
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse

User = get_user_model()


class IdentitySimulationTests(TestCase):
    def setUp(self) -> None:
        self.c = Client()
        self.user = User.objects.create_user(username="u", password="p")
        self.activity_pk = None

        # Console notice for developers
        print(
            "[INFO] Note: The test "
            "activities.tests.GatewayModesTests.test_enroll_via_wcs_then_pay_via_lingo "
            "will still fail until the demo user 'parent' is created with "
            "id_verified=True in the test context. "
            "This file only corrects accounts/tests/test_identity.py."
        )

    def _ensure_activity_pk(self) -> None:
        if self.activity_pk is not None:
            return
        self.c.login(username="u", password="p")

        r_list = self.c.get("/activities/", follow=False)
        self.assertEqual(
            r_list.status_code, 200,
            "Activities list page not reachable at /activities/ (status != 200)."
        )
        html = r_list.content.decode("utf-8", "ignore")

        pks = re.findall(r"/activities/(\d+)/", html)
        pks = [pk for pk in pks if f"/activities/{pk}/inscrire/" not in html]
        if not pks:
            pks = re.findall(r"/activities/(\d+)/inscrire/", html)

        self.assertTrue(pks, "No Activity links found on /activities/ page.")

        picked = None
        for pk in pks:
            resp = self.c.get(f"/activities/{pk}/", follow=False)
            if resp.status_code == 200:
                picked = int(pk)
                break
        if picked is None:
            picked = int(pks[0])

        self.activity_pk = picked

    @override_settings(IDENTITY_BACKEND="simulation")
    def test_block_then_verify_then_allow(self) -> None:
        self._ensure_activity_pk()
        enroll_url = f"/activities/{self.activity_pk}/inscrire/"

        r = self.c.post(enroll_url, follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts_verify_identity"), r["Location"])

        verify = reverse("accounts_verify_identity")
        r2 = self.c.post(verify, {"next": enroll_url}, follow=False)
        self.assertEqual(r2.status_code, 302)

        self.user.refresh_from_db()
        self.assertTrue(self.user.profile.id_verified)

        r3 = self.c.post(enroll_url, follow=False)
        self.assertNotIn(verify, r3.get("Location", ""))

    @override_settings(IDENTITY_BACKEND="simulation")
    def test_verify_redirect_sanitizes_next_post_only(self) -> None:
        self.c.login(username="u", password="p")
        verify = reverse("accounts_verify_identity")
        r = self.c.post(verify, {"next": "/activities/123/inscrire/"}, follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/activities/123/")


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
        AUTHENTIC_TOKEN_URL="https://idp.example/token",
        AUTHENTIC_USERINFO_URL="https://idp.example/userinfo",
        AUTHENTIC_CLIENT_ID="demo-client",
        AUTHENTIC_CLIENT_SECRET="s",
        AUTHENTIC_REDIRECT_URI="http://testserver/accounts/verify/callback/",
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
