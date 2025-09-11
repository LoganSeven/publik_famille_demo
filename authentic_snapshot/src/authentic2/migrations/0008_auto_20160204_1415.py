from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0007_auto_20150523_0028'),
    ]

    operations = [
        migrations.AlterField(
            model_name='passwordreset',
            name='user',
            field=models.ForeignKey(
                verbose_name='user', to=settings.AUTH_USER_MODEL, unique=True, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
    ]
