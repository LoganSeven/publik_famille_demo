from django.db import migrations, models


def set_odcclient_default_claims(apps, schema_editor):
    OIDCClient = apps.get_model('authentic2_idp_oidc', 'OIDCClient')
    OIDCClaim = apps.get_model('authentic2_idp_oidc', 'OIDCClaim')
    for oidcclient in OIDCClient.objects.all():
        OIDCClaim.objects.create(
            client=oidcclient, name='preferred_username', value='django_user_username', scopes='profile'
        )
        OIDCClaim.objects.create(
            client=oidcclient, name='given_name', value='django_user_first_name', scopes='profile'
        )
        OIDCClaim.objects.create(
            client=oidcclient, name='family_name', value='django_user_last_name', scopes='profile'
        )
        OIDCClaim.objects.create(client=oidcclient, name='email', value='django_user_email', scopes='email')
        OIDCClaim.objects.create(
            client=oidcclient, name='email_verified', value='django_user_email_verified', scopes='email'
        )


def unset_odcclient_default_claims(apps, schema_editor):
    OIDCClient = apps.get_model('authentic2_idp_oidc', 'OIDCClient')
    for oidcclient in OIDCClient.objects.all():
        oidcclient.oidcclaim_set.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_oidc', '0009_auto_20180313_1156'),
    ]

    operations = [
        migrations.CreateModel(
            name='OIDCClaim',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(max_length=128, verbose_name='attribute name', blank=True)),
                ('value', models.CharField(max_length=128, verbose_name='value of attribute', blank=True)),
                ('scopes', models.CharField(max_length=128, verbose_name='attribute scopes', blank=True)),
                (
                    'client',
                    models.ForeignKey(
                        verbose_name='client', to='authentic2_idp_oidc.OIDCClient', on_delete=models.CASCADE
                    ),
                ),
            ],
        ),
        migrations.RunPython(set_odcclient_default_claims, unset_odcclient_default_claims),
    ]
