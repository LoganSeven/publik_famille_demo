# accounts/migrations/0002_backfill_profiles.py
from django.db import migrations

def backfill_profiles(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    for u in User.objects.all().only("id"):
        UserProfile.objects.get_or_create(user_id=u.id)

class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(backfill_profiles, migrations.RunPython.noop),
    ]
