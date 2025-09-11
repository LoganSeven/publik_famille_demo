from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('attribute_aggregator', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='AttributePolicy',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(unique=True, max_length=100)),
                ('enabled', models.BooleanField(default=False, verbose_name='Enabled')),
                (
                    'ask_consent_attributes',
                    models.BooleanField(
                        default=True, verbose_name='Ask the user consent before forwarding attributes'
                    ),
                ),
                (
                    'allow_attributes_selection',
                    models.BooleanField(
                        default=True, verbose_name='Allow the user to select the forwarding attributes'
                    ),
                ),
                (
                    'forward_attributes_from_push_sources',
                    models.BooleanField(default=False, verbose_name='Forward pushed attributes'),
                ),
                (
                    'map_attributes_from_push_sources',
                    models.BooleanField(default=False, verbose_name='Map forwarded pushed attributes'),
                ),
                (
                    'output_name_format',
                    models.CharField(
                        default=('urn:oasis:names:tc:SAML:2.0:attrname-format:uri', 'SAMLv2 URI'),
                        max_length=100,
                        verbose_name='Output name format',
                        choices=[
                            ('urn:oasis:names:tc:SAML:2.0:attrname-format:uri', 'SAMLv2 URI'),
                            ('urn:oasis:names:tc:SAML:2.0:attrname-format:basic', 'SAMLv2 BASIC'),
                        ],
                    ),
                ),
                (
                    'output_namespace',
                    models.CharField(
                        default=('Default', 'Default'),
                        max_length=100,
                        verbose_name='Output namespace',
                        choices=[
                            ('Default', 'Default'),
                            (
                                'http://schemas.xmlsoap.org/ws/2005/05/identity/claims',
                                'http://schemas.xmlsoap.org/ws/2005/05/identity/claims',
                            ),
                        ],
                    ),
                ),
                (
                    'filter_source_of_filtered_attributes',
                    models.BooleanField(
                        default=False,
                        verbose_name='Filter by source and per attribute the forwarded pushed attributes',
                    ),
                ),
                (
                    'map_attributes_of_filtered_attributes',
                    models.BooleanField(default=False, verbose_name='Map filtered attributes'),
                ),
                (
                    'send_error_and_no_attrs_if_missing_required_attrs',
                    models.BooleanField(
                        default=False, verbose_name='Send an error when a required attribute is missing'
                    ),
                ),
                (
                    'attribute_filter_for_sso_from_push_sources',
                    models.ForeignKey(
                        related_name='filter attributes of push sources with list',
                        verbose_name='Filter by attribute names the forwarded pushed attributes',
                        blank=True,
                        to='attribute_aggregator.AttributeList',
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    'attribute_list_for_sso_from_pull_sources',
                    models.ForeignKey(
                        related_name='attributes from pull sources',
                        verbose_name='Pull attributes list',
                        blank=True,
                        to='attribute_aggregator.AttributeList',
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    'source_filter_for_sso_from_push_sources',
                    models.ManyToManyField(
                        related_name='filter attributes of push sources with sources',
                        null=True,
                        verbose_name='Filter by source the forwarded pushed attributes',
                        to='attribute_aggregator.AttributeSource',
                        blank=True,
                    ),
                ),
            ],
            options={
                'verbose_name': 'attribute policy',
                'verbose_name_plural': 'attribute policies',
            },
            bases=(models.Model,),
        ),
    ]
