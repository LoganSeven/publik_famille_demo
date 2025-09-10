from django.db import migrations


def forward(apps, schema_editor):
    AgendaPricing = apps.get_model('pricing', 'AgendaPricing')
    AgendaPricingCriteriaCategory = apps.get_model('pricing', 'AgendaPricingCriteriaCategory')
    PricingCriteriaCategory = apps.get_model('pricing', 'PricingCriteriaCategory')
    for agenda_pricing in AgendaPricing.objects.all():
        for category in PricingCriteriaCategory.objects.filter(pricing=agenda_pricing.pricing):
            AgendaPricingCriteriaCategory.objects.create(
                agenda_pricing=agenda_pricing,
                category=category.category,
                order=category.order,
            )
        agenda_pricing.criterias.set(agenda_pricing.pricing.criterias.all())
        agenda_pricing.extra_variables = agenda_pricing.pricing.extra_variables
        agenda_pricing.kind = agenda_pricing.pricing.kind
        agenda_pricing.reduction_rate = agenda_pricing.pricing.reduction_rate
        agenda_pricing.effort_rate_target = agenda_pricing.pricing.effort_rate_target
        agenda_pricing.save()


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0016_merge_models'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
