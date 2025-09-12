# accounts/signals.py
from django.contrib.auth import get_user_model
from django.db.utils import OperationalError, ProgrammingError
from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from django.apps import apps
from .models import UserProfile

User = get_user_model()

@receiver(post_save, sender=User)
def create_profile_on_user_create(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        UserProfile.objects.get_or_create(user=instance)
    except (OperationalError, ProgrammingError):
        # Table may not exist yet during bootstrap
        pass

@receiver(post_migrate)
def backfill_profiles(sender, **kwargs):
    try:
        if not apps.is_installed("accounts"):
            return
        users = User.objects.all().only("id")
        existing = set(UserProfile.objects.values_list("user_id", flat=True))
        to_create = [UserProfile(user=u) for u in users if u.id not in existing]
        if to_create:
            UserProfile.objects.bulk_create(to_create, ignore_conflicts=True)
    except (OperationalError, ProgrammingError):
        pass
