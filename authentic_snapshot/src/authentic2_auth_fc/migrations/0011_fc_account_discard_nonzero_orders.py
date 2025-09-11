from django.db import migrations


def delete_nonzero_fc_accounts(apps, schema_editor):
    FcAccount = apps.get_model('authentic2_auth_fc', 'FcAccount')

    FcAccount.objects.exclude(order=0).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_auth_fc', '0010_fcauthenticator_jwks'),
    ]

    operations = [
        migrations.RunPython(delete_nonzero_fc_accounts, reverse_code=migrations.RunPython.noop),
    ]
