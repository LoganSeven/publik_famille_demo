from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('snapshot', '0003_payer'),
        ('invoicing', '0128_payer'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Payer',
        ),
    ]
