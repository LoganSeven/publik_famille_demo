from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0108_credit_line_details'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='draftjournalline',
            index=models.Index(fields=['pool', 'status'], name='invoicing_d_pool_id_3c54d9_idx'),
        ),
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(
                fields=['pool', 'status', 'error_status'], name='invoicing_j_pool_id_734905_idx'
            ),
        ),
    ]
