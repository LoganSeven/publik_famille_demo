from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0008_injected_line'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoiceline',
            name='user_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='user_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
