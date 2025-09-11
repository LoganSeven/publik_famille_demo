# activities/migrations/0002_enrollment_wcs_id.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("activities", "0002_alter_enrollment_requested_on"),  # adapte si ta dernière migration diffère
    ]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="wcs_id",
            field=models.CharField(max_length=64, null=True, blank=True),
        ),
    ]
