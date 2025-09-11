from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_oidc', '0007_oidcclient_has_api_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='oidcclient',
            name='idtoken_duration',
            field=models.DurationField(
                verbose_name='time during which the token is valid', blank=True, null=True, default=None
            ),
        ),
    ]
