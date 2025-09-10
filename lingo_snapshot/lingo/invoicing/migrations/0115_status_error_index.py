from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0114_user_external_id_index'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='draftjournalline',
            index=models.Index(
                condition=models.Q(('pricing_data__error__isnull', False)),
                fields=['pool_id'],
                name='invoicing_djl_error_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(
                condition=models.Q(('pricing_data__error__isnull', False)),
                fields=['pool_id'],
                name='invoicing_jl_error_idx',
            ),
        ),
    ]
