import django.contrib.postgres.indexes
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0109_journal_line_index'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='draftjournalline',
            name='invoicing_d_pool_id_3c54d9_idx',
        ),
        migrations.RemoveIndex(
            model_name='journalline',
            name='invoicing_j_pool_id_734905_idx',
        ),
        migrations.AddIndex(
            model_name='creditline',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['details'], name='invoicing_c_details_5f5bcd_gin'
            ),
        ),
        migrations.AddIndex(
            model_name='draftjournalline',
            index=models.Index(fields=['pool', 'status'], name='invoicing_d_pool_id_fe8ceb_idx'),
        ),
        migrations.AddIndex(
            model_name='invoiceline',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['details'], name='invoicing_i_details_364261_gin'
            ),
        ),
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(
                fields=['pool', 'status', 'error_status'], name='invoicing_j_pool_id_a7208a_idx'
            ),
        ),
    ]
