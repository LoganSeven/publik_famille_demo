from django.db import migrations

OLD_DEFAULT_CLAIMS_MAPPING = {
    'email': 'django_user_email',
    'email_verified': 'django_user_email_verified',
    'family_name': 'django_user_last_name',
    'given_name': 'django_user_first_name',
    'preferred_username': 'django_user_username',
}


def set_oidcclient_default_preferred_username_as_identifier(apps, schema_editor):
    OIDCClient = apps.get_model('authentic2_idp_oidc', 'OIDCClient')
    OIDCClaim = apps.get_model('authentic2_idp_oidc', 'OIDCClaim')
    for oidcclient in OIDCClient.objects.all():
        claims = oidcclient.oidcclaim_set.values_list('name', 'value')
        # check if default config
        if set(OLD_DEFAULT_CLAIMS_MAPPING.items()).symmetric_difference(claims):
            continue
        pref_username_claim = OIDCClaim.objects.get(name='preferred_username', client=oidcclient)
        if pref_username_claim.value != 'django_user_identifier':
            pref_username_claim.value = 'django_user_identifier'
            pref_username_claim.save()


def unset_oidcclient_default_preferred_username_as_identifier(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_oidc', '0010_oidcclaim'),
    ]

    operations = [
        migrations.RunPython(
            set_oidcclient_default_preferred_username_as_identifier,
            unset_oidcclient_default_preferred_username_as_identifier,
        )
    ]
