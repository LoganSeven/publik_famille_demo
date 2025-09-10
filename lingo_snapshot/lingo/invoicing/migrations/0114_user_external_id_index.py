from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0113_date_payment'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='creditline',
            index=models.Index(fields=['user_external_id'], name='invoicing_c_user_ex_e7c4c6_idx'),
        ),
        migrations.AddIndex(
            model_name='invoiceline',
            index=models.Index(fields=['user_external_id'], name='invoicing_i_user_ex_9a77d8_idx'),
        ),
    ]
