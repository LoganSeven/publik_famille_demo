import datetime

import pytest
from django.core.management import call_command
from django.utils.timezone import now

from lingo.invoicing.models import Campaign, CampaignAsyncJob, Pool, PoolAsyncJob, Regie

pytestmark = pytest.mark.django_db


def test_clear_jobs():
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
    )

    CampaignAsyncJob.objects.create(campaign=campaign, status='registered')
    CampaignAsyncJob.objects.create(campaign=campaign, status='running')
    cfailed_job = CampaignAsyncJob.objects.create(campaign=campaign, status='failed')
    ccompleted_job = CampaignAsyncJob.objects.create(campaign=campaign, status='completed')

    PoolAsyncJob.objects.create(pool=pool, status='registered')
    PoolAsyncJob.objects.create(pool=pool, status='running')
    pfailed_job = PoolAsyncJob.objects.create(pool=pool, status='failed')
    pcompleted_job = PoolAsyncJob.objects.create(pool=pool, status='completed')

    # too soon
    call_command('clear_jobs')
    assert CampaignAsyncJob.objects.count() == 4
    assert PoolAsyncJob.objects.count() == 4

    # still too soon
    CampaignAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=2, minutes=-1))
    PoolAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=2, minutes=-1))
    call_command('clear_jobs')
    assert CampaignAsyncJob.objects.count() == 4
    assert PoolAsyncJob.objects.count() == 4

    # ok, two days after job is completed, but too soon for errors
    CampaignAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=2))
    PoolAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=2))
    call_command('clear_jobs')
    assert CampaignAsyncJob.objects.count() == 3
    assert CampaignAsyncJob.objects.filter(pk=ccompleted_job.pk).exists() is False
    assert PoolAsyncJob.objects.count() == 3
    assert PoolAsyncJob.objects.filter(pk=pcompleted_job.pk).exists() is False

    # too soon for errors
    CampaignAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=10, minutes=-1))
    PoolAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=10, minutes=-1))
    call_command('clear_jobs')
    assert CampaignAsyncJob.objects.count() == 3
    assert CampaignAsyncJob.objects.filter(pk=ccompleted_job.pk).exists() is False
    assert PoolAsyncJob.objects.count() == 3
    assert PoolAsyncJob.objects.filter(pk=pcompleted_job.pk).exists() is False

    # ok, ten days after job has failed
    CampaignAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=10))
    PoolAsyncJob.objects.update(last_update_timestamp=now() - datetime.timedelta(days=10))
    call_command('clear_jobs')
    assert CampaignAsyncJob.objects.count() == 2
    assert CampaignAsyncJob.objects.filter(pk=ccompleted_job.pk).exists() is False
    assert CampaignAsyncJob.objects.filter(pk=cfailed_job.pk).exists() is False
    assert PoolAsyncJob.objects.count() == 2
    assert PoolAsyncJob.objects.filter(pk=pcompleted_job.pk).exists() is False
    assert PoolAsyncJob.objects.filter(pk=pfailed_job.pk).exists() is False


def test_campaign_generate_job(settings):
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='generate',
        params={
            'draft_pool_id': 0,
        },
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'draft pool not found'

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='generate',
        params={
            'draft_pool_id': pool.pk,
        },
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'draft pool not found'

    pool.draft = True
    pool.status = 'running'
    pool.save()
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='generate',
        params={
            'draft_pool_id': pool.pk,
        },
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool wrong status running (wanted: registered)'

    # check max running jobs
    pool.status = 'registered'
    pool.save()
    for dummy in range(settings.CAMPAIGN_MAX_RUNNING_JOBS):
        CampaignAsyncJob.objects.create(campaign=campaign, status='running')
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='generate',
        params={
            'draft_pool_id': pool.pk,
        },
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'

    settings.CAMPAIGN_MAX_RUNNING_JOBS += 1
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'


def test_campaign_assign_credits_job(settings):
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='assign_credits',
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'campaign not finalized'

    # check max running jobs
    campaign.finalized = True
    campaign.save()
    for dummy in range(settings.CAMPAIGN_MAX_RUNNING_JOBS):
        CampaignAsyncJob.objects.create(campaign=campaign, status='running')
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='assign_credits',
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'

    settings.CAMPAIGN_MAX_RUNNING_JOBS += 1
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'


def test_campaign_populate_from_draft_job(settings):
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    draft_pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    final_pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': 0, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'draft pool not found'

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'draft pool not found'

    draft_pool.draft = True
    draft_pool.status = 'running'
    draft_pool.save()
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool wrong status running (wanted: completed)'

    other_draft_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    draft_pool.status = 'completed'
    draft_pool.save()
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'more recent draft pool exists'

    other_draft_pool.delete()
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': 0},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'final pool not found'

    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': draft_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'final pool not found'

    final_pool.draft = False
    final_pool.status = 'running'
    final_pool.save()
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'final pool wrong status running (wanted: registered)'

    # check max running jobs
    final_pool.status = 'registered'
    final_pool.save()
    for dummy in range(settings.CAMPAIGN_MAX_RUNNING_JOBS):
        CampaignAsyncJob.objects.create(campaign=campaign, status='running')
    job = CampaignAsyncJob.objects.create(
        campaign=campaign,
        action='populate_from_draft',
        params={'draft_pool_id': draft_pool.pk, 'final_pool_id': final_pool.pk},
    )
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'

    settings.CAMPAIGN_MAX_RUNNING_JOBS += 1
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'


def test_pool_generate_invoices_job(settings):
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )

    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='generate_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool is not draft'

    pool.draft = True
    pool.status = 'registered'
    pool.save()
    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='generate_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool wrong status registered (wanted: running)'

    # check max running jobs
    pool.status = 'running'
    pool.save()
    for dummy in range(settings.POOL_MAX_RUNNING_JOBS):
        PoolAsyncJob.objects.create(pool=pool, status='running')
    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='generate_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'

    settings.POOL_MAX_RUNNING_JOBS += 1
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'


def test_pool_finalize_invoices_job(settings):
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )

    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool is not draft'

    pool.draft = True
    pool.status = 'registered'
    pool.save()
    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.exception == 'pool wrong status registered (wanted: running)'

    # check max running jobs
    pool.status = 'running'
    pool.save()
    for dummy in range(settings.POOL_MAX_RUNNING_JOBS):
        PoolAsyncJob.objects.create(pool=pool, status='running')
    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'

    settings.POOL_MAX_RUNNING_JOBS += 1
    call_command('run_campaign_jobs')
    job.refresh_from_db()
    assert job.status == 'registered'
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'

    pool.status = 'running'
    pool.save()
    job = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
        status='waiting',
    )
    call_command('run_pool_jobs')
    job.refresh_from_db()
    assert job.status == 'completed'

    # check is_ready
    PoolAsyncJob.objects.all().delete()
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job1 = PoolAsyncJob.objects.create(pool=pool, action='generate_invoices', status='running')
    job2 = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
        status='waiting',
    )
    other_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job3 = PoolAsyncJob.objects.create(pool=other_pool, action='generate_invoices', status='registered')
    assert job1.is_ready is True
    assert job2.is_ready is False
    assert job3.is_ready is True
    call_command('run_pool_jobs')
    job1.refresh_from_db()
    job2.refresh_from_db()
    job3.refresh_from_db()
    assert job1.status == 'running'
    assert job2.status == 'waiting'
    assert job3.status == 'completed'

    PoolAsyncJob.objects.all().delete()
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job1 = PoolAsyncJob.objects.create(pool=pool, action='generate_invoices', status='completed')
    job2 = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
        status='waiting',
    )
    other_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job3 = PoolAsyncJob.objects.create(pool=other_pool, action='generate_invoices', status='registered')
    assert job1.is_ready is True
    assert job2.is_ready is True
    assert job3.is_ready is True
    call_command('run_pool_jobs')
    job1.refresh_from_db()
    job2.refresh_from_db()
    job3.refresh_from_db()
    assert job1.status == 'completed'
    assert job2.status == 'completed'
    assert job3.status == 'registered'

    PoolAsyncJob.objects.all().delete()
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job1 = PoolAsyncJob.objects.create(pool=pool, action='generate_invoices', status='failed')
    job2 = PoolAsyncJob.objects.create(
        pool=pool,
        action='finalize_invoices',
        status='waiting',
    )
    other_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    job3 = PoolAsyncJob.objects.create(pool=other_pool, action='generate_invoices', status='registered')
    assert job1.is_ready is True
    assert job2.is_ready is True
    assert job3.is_ready is True
    call_command('run_pool_jobs')
    job1.refresh_from_db()
    job2.refresh_from_db()
    job3.refresh_from_db()
    assert job1.status == 'failed'
    assert job2.status == 'failed'
    assert job3.status == 'registered'
