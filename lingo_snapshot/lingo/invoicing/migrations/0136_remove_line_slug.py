from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0135_origin'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='creditline',
            name='slug',
        ),
        migrations.RemoveField(
            model_name='draftinvoiceline',
            name='slug',
        ),
        migrations.RemoveField(
            model_name='invoiceline',
            name='slug',
        ),
    ]
