from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_oidc', '0008_oidcclient_idtoken_duration'),
    ]

    operations = [
        migrations.AddField(
            model_name='oidcclient',
            name='frontchannel_logout_uri',
            field=models.URLField(verbose_name='frontchannel logout URI', blank=True),
        ),
        migrations.AddField(
            model_name='oidcclient',
            name='frontchannel_timeout',
            field=models.PositiveIntegerField(null=True, verbose_name='frontchannel timeout', blank=True),
        ),
    ]
