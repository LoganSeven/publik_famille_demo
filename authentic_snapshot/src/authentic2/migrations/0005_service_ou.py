from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.RBAC_OU_MODEL),
        ('authentic2', '0004_service'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='ou',
            field=models.ForeignKey(
                blank=True, to=settings.RBAC_OU_MODEL, null=True, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='service',
            unique_together={('slug', 'ou')},
        ),
    ]
