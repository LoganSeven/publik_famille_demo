from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_auth_oidc', '0003_oidcprovider_show'),
    ]

    operations = [
        migrations.AlterField(
            model_name='oidcprovider',
            name='strategy',
            field=models.CharField(
                max_length=32,
                verbose_name='strategy',
                choices=[
                    (
                        'create',
                        'create if account matching on email address failed (matching will fail if '
                        'global and provider\'s ou-wise email uniqueness is deactivated)',
                    ),
                    ('find-uuid', 'use sub to find existing user through UUID'),
                    ('find-username', 'use sub to find existing user through username'),
                    (
                        'find-email',
                        'use email claim (or sub if claim is absent) to find existing user through email',
                    ),
                    ('none', 'none'),
                ],
            ),
        ),
    ]
