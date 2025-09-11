from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0004_user_ou'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='ou',
            field=models.ForeignKey(
                verbose_name='organizational unit',
                blank=True,
                to=settings.RBAC_OU_MODEL,
                null=True,
                on_delete=models.CASCADE,
            ),
            preserve_default=True,
        ),
    ]
