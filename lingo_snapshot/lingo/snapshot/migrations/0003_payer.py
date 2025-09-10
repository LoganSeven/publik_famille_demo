from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0128_payer'),
        ('snapshot', '0002_snapshot_models'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PayerSnapshot',
        ),
    ]
