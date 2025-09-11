from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('idp', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attributepolicy',
            name='attribute_filter_for_sso_from_push_sources',
            field=models.ForeignKey(
                related_name='+',
                verbose_name='Filter by attribute names the forwarded pushed attributes',
                blank=True,
                to='attribute_aggregator.AttributeList',
                null=True,
                on_delete=models.CASCADE,
            ),
        ),
        migrations.AlterField(
            model_name='attributepolicy',
            name='attribute_list_for_sso_from_pull_sources',
            field=models.ForeignKey(
                related_name='+',
                verbose_name='Pull attributes list',
                blank=True,
                to='attribute_aggregator.AttributeList',
                null=True,
                on_delete=models.CASCADE,
            ),
        ),
        migrations.AlterField(
            model_name='attributepolicy',
            name='source_filter_for_sso_from_push_sources',
            field=models.ManyToManyField(
                to='attribute_aggregator.AttributeSource',
                verbose_name='Filter by source the forwarded pushed attributes',
                blank=True,
            ),
        ),
    ]
