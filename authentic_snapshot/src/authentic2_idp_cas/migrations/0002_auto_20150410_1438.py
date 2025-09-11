from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('authentic2_idp_cas', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='user',
            field=models.ForeignKey(
                blank=True,
                to=settings.AUTH_USER_MODEL,
                max_length=128,
                null=True,
                verbose_name='user',
                on_delete=models.CASCADE,
            ),
            preserve_default=True,
        ),
    ]
