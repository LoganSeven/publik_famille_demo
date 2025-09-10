from django.db import migrations

import lingo.utils.fields


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0066_invoice_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='invoice_custom_text',
            field=lingo.utils.fields.RichTextField(
                blank=True,
                help_text='Displayed under the address and additional information blocks.',
                null=True,
                verbose_name='Custom text in invoice',
            ),
        ),
    ]
