from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0011_payer_variables'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='pricing',
            name='payer_variables',
        ),
    ]
