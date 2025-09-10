import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0136_remove_line_slug'),
        ('basket', '0012_payer_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='other_payer_credits_draft',
            field=models.ManyToManyField(related_name='+', to='invoicing.draftinvoice'),
        ),
        migrations.AlterField(
            model_name='basket',
            name='draft_invoice',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name='+', to='invoicing.draftinvoice'
            ),
        ),
    ]
