from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0093_printing'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='usable',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='usable',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='usable',
            field=models.BooleanField(default=True),
        ),
    ]
