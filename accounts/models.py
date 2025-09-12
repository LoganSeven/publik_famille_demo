# accounts/models.py
from django.conf import settings
from django.db import models

class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    id_verified = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.user} profile"
