from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0122_credit_cancellation_reason'),
    ]

    operations = [
        migrations.RenameField(
            model_name='credit',
            old_name='new_cancellation_reason',
            new_name='cancellation_reason',
        ),
        migrations.RemoveField(
            model_name='credit',
            name='old_cancellation_reason',
        ),
    ]
