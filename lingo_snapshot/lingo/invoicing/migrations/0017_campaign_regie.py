import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0016_campaign_regie'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='regie',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.Regie'),
        ),
    ]
