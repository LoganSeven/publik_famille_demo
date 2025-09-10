from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0104_auto_20240916_1029'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditline',
            name='form_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='form_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='form_url',
            field=models.URLField(blank=True),
        ),
    ]
