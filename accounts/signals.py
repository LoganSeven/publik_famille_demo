# accounts/signals.py
"""
Signals for the accounts application.

This module ensures that each user has an associated
UserProfile instance. It defines signals that create a
profile upon user creation and backfill missing profiles
after database migrations.
"""

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError, ProgrammingError
from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from django.apps import apps
from .models import UserProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_profile_on_user_create(sender, instance, created, **kwargs):
    """
    Create a UserProfile when a new user is created.

    Parameters
    ----------
    sender : Model
        The model class sending the signal (User).
    instance : User
        The user instance that was created or updated.
    created : bool
        True if a new user instance was created, False otherwise.
    **kwargs : dict
        Additional keyword arguments provided by the signal.

    Notes
    -----
    - Wrapped in a try/except to handle migration states where
      the UserProfile table may not exist yet.
    """
    if not created:
        return
    try:
        UserProfile.objects.get_or_create(user=instance)
    except (OperationalError, ProgrammingError):
        # Avoid errors during migration when the table may not yet exist
        pass


@receiver(post_migrate)
def backfill_profiles(sender, **kwargs):
    """
    Ensure all existing users have associated UserProfile records.

    Parameters
    ----------
    sender : AppConfig
        The app configuration sending the signal.
    **kwargs : dict
        Additional keyword arguments provided by the signal.

    Notes
    -----
    - Executed after migrations are applied.
    - Creates profiles for users without one.
    - Wrapped in a try/except to handle early migration states.
    """
    try:
        if not apps.is_installed("accounts"):
            return

        # Fetch all user IDs
        users = User.objects.all().only("id")

        # Get IDs of users who already have a profile
        existing = set(UserProfile.objects.values_list("user_id", flat=True))

        # Prepare profiles for users missing one
        to_create = [UserProfile(user=u) for u in users if u.id not in existing]

        if to_create:
            UserProfile.objects.bulk_create(to_create, ignore_conflicts=True)
    except (OperationalError, ProgrammingError):
        # Avoid errors during migrations if tables are not ready
        pass
