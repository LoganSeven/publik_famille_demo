from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0127_payer'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='payer',
            name='snapshot',
        ),
        migrations.RemoveField(
            model_name='regie',
            name='payer',
        ),
    ]
