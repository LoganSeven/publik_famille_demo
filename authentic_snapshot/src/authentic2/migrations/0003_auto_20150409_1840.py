from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('authentic2', '0002_auto_20150320_1418'),
    ]

    operations = [
        migrations.AlterField(
            model_name='deleteduser',
            name='user',
            field=models.ForeignKey(
                verbose_name='user', to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='passwordreset',
            name='user',
            field=models.ForeignKey(
                verbose_name='user', to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='userexternalid',
            name='user',
            field=models.ForeignKey(
                verbose_name='user', to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
    ]
