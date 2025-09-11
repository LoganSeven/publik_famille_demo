from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_auth_oidc', '0005_oidcprovider_slug'),
    ]

    operations = [
        migrations.AddField(
            model_name='oidcprovider',
            name='claims_parameter_supported',
            field=models.BooleanField(default=False, verbose_name='Claims parameter supported'),
        ),
    ]
