import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(
                help_text=(
                    'Required, 255 characters or fewer. Only letters, numbers, and @, ., +, -, or _'
                    ' characters.'
                ),
                unique=True,
                max_length=255,
                verbose_name='username',
                validators=[
                    django.core.validators.RegexValidator(
                        '^[\\w.@+-]+$', 'Enter a valid username.', 'invalid'
                    )
                ],
            ),
            preserve_default=True,
        ),
    ]
