from django.db import migrations, models

import lingo.utils.fields


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0060_journal_line_booking'),
    ]

    operations = [
        migrations.CreateModel(
            name='AppearanceSettings',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('logo', models.ImageField(blank=True, null=True, upload_to='logo', verbose_name='Logo')),
                ('address', lingo.utils.fields.RichTextField(blank=True, null=True, verbose_name='Address')),
                (
                    'extra_info',
                    lingo.utils.fields.RichTextField(
                        blank=True,
                        help_text='Displayed below the address block.',
                        null=True,
                        verbose_name='Additional information',
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name='regie',
            name='invoice_custom_text',
            field=lingo.utils.fields.RichTextField(
                blank=True, help_text='Displayed in footer.', null=True, verbose_name='Custom text in invoice'
            ),
        ),
    ]
