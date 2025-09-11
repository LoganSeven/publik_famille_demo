from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('django_rbac', '0003_add_max_aggregate_for_postgres'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='permissions',
            field=models.ManyToManyField(related_name='roles', to=settings.RBAC_PERMISSION_MODEL, blank=True),
            preserve_default=True,
        ),
    ]
