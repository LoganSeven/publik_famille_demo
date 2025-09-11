from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0012_user_modified'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_verified',
            field=models.BooleanField(default=False, verbose_name='email verified'),
        ),
    ]
