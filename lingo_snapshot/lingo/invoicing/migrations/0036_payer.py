import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0035_payer'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='payer',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.payer'
            ),
        ),
    ]
