from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0034_draft_invoice_uuid'),
    ]

    operations = [
        migrations.CreateModel(
            name='Payer',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Description')),
                ('carddef_reference', models.CharField(max_length=150, verbose_name='Card Model')),
                ('cached_carddef_json', models.JSONField(blank=True, default=dict)),
                (
                    'payer_external_id_prefix',
                    models.CharField(blank=True, max_length=250, verbose_name='Prefix for payer external id'),
                ),
                (
                    'payer_external_id_template',
                    models.CharField(
                        blank=True,
                        help_text='To get payer external id from user external id',
                        max_length=1000,
                        verbose_name='Template for payer external id',
                    ),
                ),
                ('user_fields_mapping', models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.AlterModelOptions(
            name='payer',
            options={'ordering': ['label']},
        ),
    ]
