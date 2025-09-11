from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('saml', '0008_alter_foreign_keys'),
    ]

    operations = [
        migrations.AlterField(
            model_name='libertyidentityprovider',
            name='liberty_provider',
            field=models.IntegerField(null=False),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='libertyserviceprovider',
            name='liberty_provider',
            field=models.IntegerField(null=False),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='libertyidentityprovider',
            name='new_liberty_provider',
            field=models.OneToOneField(
                related_name='identity_provider',
                primary_key=True,
                serialize=False,
                to='saml.LibertyProvider',
                on_delete=models.CASCADE,
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='libertyserviceprovider',
            name='new_liberty_provider',
            field=models.OneToOneField(
                related_name='service_provider',
                primary_key=True,
                serialize=False,
                to='saml.LibertyProvider',
                on_delete=models.CASCADE,
            ),
            preserve_default=True,
        ),
    ]
