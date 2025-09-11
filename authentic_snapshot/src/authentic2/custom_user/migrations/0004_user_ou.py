from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.RBAC_OU_MODEL),
        ('custom_user', '0003_auto_20150504_1410'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ou',
            field=models.ForeignKey(
                blank=True, to=settings.RBAC_OU_MODEL, null=True, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
    ]
