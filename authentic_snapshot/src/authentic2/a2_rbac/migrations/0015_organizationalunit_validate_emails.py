from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0014_auto_20170711_1024'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationalunit',
            name='validate_emails',
            field=models.BooleanField(
                blank=True,
                default=False,
                verbose_name='Validate emails when modified in backoffice',
                help_text=(
                    "If checked, an agent in backoffice won't be able to directly edit the user's "
                    'email address, instead a confirmation link will be sent to the newly-declared '
                    'address for the change to be effective.'
                ),
            ),
        )
    ]
