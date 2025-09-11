from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0017_organizationalunit_user_can_reset_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationalunit',
            name='user_add_password_policy',
            field=models.IntegerField(
                default=0,
                verbose_name='User creation password policy',
                choices=[(0, 'Send reset link'), (1, 'Manual password definition')],
            ),
        ),
    ]
