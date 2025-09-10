from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0031_standalone_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoice',
            name='uuid',
            field=models.UUIDField(null=True),
        ),
    ]
