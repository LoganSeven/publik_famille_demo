from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0043_total_amount'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='injectedline',
            name='total_amount',
        ),
    ]
