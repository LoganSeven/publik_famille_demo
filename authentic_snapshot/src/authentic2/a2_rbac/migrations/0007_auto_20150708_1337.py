from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0006_auto_20150619_1056'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='permissions',
            field=models.ManyToManyField(related_name='roles', to=settings.RBAC_PERMISSION_MODEL, blank=True),
            preserve_default=True,
        ),
    ]
