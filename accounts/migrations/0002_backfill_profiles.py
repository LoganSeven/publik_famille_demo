# accounts/migrations/0002_backfill_profiles.py
"""
Data migration for the accounts application.

This migration ensures that all existing users have an
associated UserProfile instance by backfilling missing
records.
"""

from django.db import migrations


def backfill_profiles(apps, schema_editor):
    """
    Create UserProfile instances for users without one.

    Parameters
    ----------
    apps : django.apps.registry.Apps
        Registry to retrieve historical models during migration.
    schema_editor : BaseDatabaseSchemaEditor
        Schema editor for applying database operations.

    Notes
    -----
    - Uses historical models to maintain consistency with
      the migration state.
    - Runs once to ensure data integrity after the initial
      creation of the UserProfile model.
    """
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    for u in User.objects.all().only("id"):
        UserProfile.objects.get_or_create(user_id=u.id)


class Migration(migrations.Migration):
    """
    Migration class for backfilling user profiles.

    Attributes
    ----------
    dependencies : list
        References the initial migration of the accounts app.
    operations : list
        Executes the backfill_profiles function, with a noop
        reverse operation.
    """

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_profiles, migrations.RunPython.noop),
    ]
