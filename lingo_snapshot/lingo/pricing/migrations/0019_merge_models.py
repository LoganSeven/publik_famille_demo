from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0018_merge_models'),
    ]

    operations = [
        migrations.RenameModel('AgendaPricing', 'Pricing'),
        migrations.RenameModel('AgendaPricingCriteriaCategory', 'PricingCriteriaCategory'),
        migrations.RenameField(
            model_name='billingdate',
            old_name='agenda_pricing',
            new_name='pricing',
        ),
        migrations.RenameField(
            model_name='pricingcriteriacategory',
            old_name='agenda_pricing',
            new_name='pricing',
        ),
        migrations.AlterUniqueTogether(
            name='pricingcriteriacategory',
            unique_together={('pricing', 'category')},
        ),
        migrations.AlterField(
            model_name='pricing',
            name='agendas',
            field=models.ManyToManyField(related_name='pricings', to='agendas.Agenda'),
        ),
        migrations.AlterField(
            model_name='pricing',
            name='categories',
            field=models.ManyToManyField(
                related_name='pricings',
                through='pricing.PricingCriteriaCategory',
                to='pricing.CriteriaCategory',
            ),
        ),
    ]
