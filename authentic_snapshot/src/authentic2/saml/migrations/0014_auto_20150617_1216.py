from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('saml', '0013_auto_20150617_1004'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='libertysessiondump',
            unique_together={('django_session_key', 'kind')},
        ),
    ]
