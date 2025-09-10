from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0017_merge_models'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='pricingcriteriacategory',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='pricingcriteriacategory',
            name='category',
        ),
        migrations.RemoveField(
            model_name='pricingcriteriacategory',
            name='pricing',
        ),
        migrations.AlterModelOptions(
            name='agendapricingcriteriacategory',
            options={'ordering': ['order']},
        ),
        migrations.RemoveField(
            model_name='agendapricing',
            name='pricing',
        ),
        migrations.AlterUniqueTogether(
            name='agendapricingcriteriacategory',
            unique_together={('agenda_pricing', 'category')},
        ),
        migrations.DeleteModel(
            name='Pricing',
        ),
        migrations.DeleteModel(
            name='PricingCriteriaCategory',
        ),
    ]
