from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0016_auto_20171208_1429'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationalunit',
            name='user_can_reset_password',
            field=models.BooleanField(
                verbose_name='Users can reset password',
                null=True,
            ),
        ),
    ]
