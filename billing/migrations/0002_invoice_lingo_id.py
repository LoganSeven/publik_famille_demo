# billing/migrations/0002_invoice_lingo_id.py
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='lingo_id',
            field=models.CharField(max_length=64, null=True, blank=True),
        ),
    ]
