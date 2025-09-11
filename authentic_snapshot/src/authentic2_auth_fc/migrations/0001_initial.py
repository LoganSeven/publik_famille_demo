from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FcAccount',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('sub', models.TextField(verbose_name='sub', db_index=True)),
                ('token', models.TextField(verbose_name='access token', default='{}')),
                ('user_info', models.TextField(null=True, verbose_name='user info', default='{}')),
                (
                    'user',
                    models.ForeignKey(
                        related_name='fc_accounts',
                        verbose_name='user',
                        to=settings.AUTH_USER_MODEL,
                        on_delete=models.CASCADE,
                    ),
                ),
            ],
        ),
    ]
