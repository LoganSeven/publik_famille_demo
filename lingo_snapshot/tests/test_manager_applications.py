import datetime
import os

import pytest
from django.core.files import File
from pyquery import PyQuery

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.export_import.models import Application, ApplicationElement
from lingo.invoicing.models import Regie
from lingo.pricing.models import CriteriaCategory, Pricing
from tests.utils import login

pytestmark = pytest.mark.django_db

TESTS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture
def application_with_icon():
    application = Application.objects.create(
        name='App 1',
        slug='app-1',
        version_number='1',
    )
    with open(os.path.join(TESTS_DATA_DIR, 'black.jpeg'), mode='rb') as fd:
        application.icon.save('black.jpeg', File(fd), save=True)
    return application


@pytest.fixture
def application_without_icon():
    application = Application.objects.create(
        name='App 2',
        slug='app-2',
        version_number='1',
    )
    return application


@pytest.mark.parametrize('icon', [True, False])
def test_agenda(app, admin_user, application_with_icon, application_without_icon, icon):
    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    ApplicationElement.objects.create(content_object=agenda2, application=application)
    agenda3 = Agenda.objects.create(label='Agenda 3')
    ApplicationElement.objects.create(content_object=agenda3, application=application)

    app = login(app)

    resp = app.get('/manage/pricing/agendas/')
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Agenda 1 [identifier: agenda-1, kind: Events] view'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(2)').text()
        == 'Agenda 2 [identifier: agenda-2, kind: Events] view'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(3)').text()
        == 'Agenda 3 [identifier: agenda-3, kind: Events] view'
    )
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert len(resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon')) == 1
        assert len(resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon')) == 1
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Agendas outside applications' in resp

    # check application view
    resp = resp.click(application.name)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img.application-logo')) == 1
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Agenda 2 [identifier: agenda-2, kind: Events] view'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(2)').text()
        == 'Agenda 3 [identifier: agenda-3, kind: Events] view'
    )
    assert len(resp.pyquery('ul.objects-list li img')) == 0

    # check elements outside applications
    resp = app.get('/manage/pricing/agendas/')
    resp = resp.click('Agendas outside applications')
    assert resp.pyquery('h2').text() == 'Agendas outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Agenda 1 [identifier: agenda-1, kind: Events] view'
    )

    # check detail page
    resp = app.get('/manage/pricing/agenda/%s/' % agenda1.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    resp = app.get('/manage/pricing/agenda/%s/' % agenda2.pk)
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check visible flag
    application.visible = False
    application.save()
    resp = app.get('/manage/pricing/agendas/')
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('ul.objects-list img')) == 0
    app.get('/manage/pricing/agendas/?application=%s' % application.slug, status=404)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda2.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_check_type(app, admin_user, application_with_icon, application_without_icon, icon):
    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    CheckTypeGroup.objects.create(label='CheckTypeGroup 1')
    check_type_group2 = CheckTypeGroup.objects.create(label='CheckTypeGroup 2')
    ApplicationElement.objects.create(content_object=check_type_group2, application=application)
    check_type_group3 = CheckTypeGroup.objects.create(label='CheckTypeGroup 3')
    ApplicationElement.objects.create(content_object=check_type_group3, application=application)

    app = login(app)

    resp = app.get('/manage/pricing/check-types/')
    assert len(resp.pyquery('.section')) == 3
    assert len(resp.pyquery('.section h3')) == 3
    assert PyQuery(resp.pyquery('.section')[0]).find('h3').text() == 'CheckTypeGroup 1 Export Delete'
    assert PyQuery(resp.pyquery('.section')[1]).find('h3').text() == 'CheckTypeGroup 2 Export Delete'
    assert PyQuery(resp.pyquery('.section')[2]).find('h3').text() == 'CheckTypeGroup 3 Export Delete'
    if icon:
        assert len(resp.pyquery('h3 img')) == 2
        assert len(PyQuery(resp.pyquery('.section')[0]).find('h3 img')) == 0
        assert len(PyQuery(resp.pyquery('.section')[1]).find('h3 img.application-icon')) == 1
        assert len(PyQuery(resp.pyquery('.section')[2]).find('h3 img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3 img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Check types outside applications' in resp

    # check application view
    resp = resp.click(application.name)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img.application-logo')) == 1
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 2
    assert len(resp.pyquery('.section h3')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h3').text() == 'CheckTypeGroup 2 Export Delete'
    assert PyQuery(resp.pyquery('.section')[1]).find('h3').text() == 'CheckTypeGroup 3 Export Delete'
    assert len(resp.pyquery('h3 img')) == 0

    # check elements outside applications
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click('Check types outside applications')
    assert resp.pyquery('h2').text() == 'Check types outside applications'
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section h3')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('h3').text() == 'CheckTypeGroup 1 Export Delete'

    # check visible flag
    application.visible = False
    application.save()
    resp = app.get('/manage/pricing/check-types/')
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('ul.objects-list img')) == 0
    app.get('/manage/pricing/check-types/?application=%s' % application.slug, status=404)


@pytest.mark.parametrize('icon', [True, False])
def test_pricing(app, admin_user, application_with_icon, application_without_icon, icon):
    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    pricing1 = Pricing.objects.create(
        label='Pricing 1',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing2 = Pricing.objects.create(
        label='Pricing 2',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    ApplicationElement.objects.create(content_object=pricing2, application=application)
    pricing3 = Pricing.objects.create(
        label='Pricing 3',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    ApplicationElement.objects.create(content_object=pricing3, application=application)

    app = login(app)

    resp = app.get('/manage/pricing/')
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Pricing 1 - From 01/09/2021 to 01/10/2021 [identifier: pricing-1]'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(2)').text()
        == 'Pricing 2 - From 01/09/2021 to 01/10/2021 [identifier: pricing-2]'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(3)').text()
        == 'Pricing 3 - From 01/09/2021 to 01/10/2021 [identifier: pricing-3]'
    )
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert len(resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon')) == 1
        assert len(resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon')) == 1
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Pricings outside applications' in resp

    # check application view
    resp = resp.click(application.name)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img.application-logo')) == 1
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Pricing 2 - From 01/09/2021 to 01/10/2021 [identifier: pricing-2]'
    )
    assert (
        resp.pyquery('ul.objects-list li:nth-child(2)').text()
        == 'Pricing 3 - From 01/09/2021 to 01/10/2021 [identifier: pricing-3]'
    )
    assert len(resp.pyquery('ul.objects-list li img')) == 0

    # check elements outside applications
    resp = app.get('/manage/pricing/')
    resp = resp.click('Pricings outside applications')
    assert resp.pyquery('h2').text() == 'Pricings outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert (
        resp.pyquery('ul.objects-list li:nth-child(1)').text()
        == 'Pricing 1 - From 01/09/2021 to 01/10/2021 [identifier: pricing-1]'
    )

    # check detail page
    resp = app.get('/manage/pricing/%s/parameters/' % pricing1.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    resp = app.get('/manage/pricing/%s/parameters/' % pricing2.pk)
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check visible flag
    application.visible = False
    application.save()
    resp = app.get('/manage/pricing/')
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('ul.objects-list img')) == 0
    app.get('/manage/pricing/?application=%s' % application.slug, status=404)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing2.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_criteria_category(app, admin_user, application_with_icon, application_without_icon, icon):
    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    CriteriaCategory.objects.create(label='CriteriaCategory 1')
    criteria_category2 = CriteriaCategory.objects.create(label='CriteriaCategory 2')
    ApplicationElement.objects.create(content_object=criteria_category2, application=application)
    criteria_category3 = CriteriaCategory.objects.create(label='CriteriaCategory 3')
    ApplicationElement.objects.create(content_object=criteria_category3, application=application)

    app = login(app)

    resp = app.get('/manage/pricing/criterias/')
    assert len(resp.pyquery('.section')) == 3
    assert len(resp.pyquery('.section h3')) == 3
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('h3').text()
        == 'CriteriaCategory 1 [criteriacategory-1] Export Delete'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('h3').text()
        == 'CriteriaCategory 2 [criteriacategory-2] Export Delete'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('h3').text()
        == 'CriteriaCategory 3 [criteriacategory-3] Export Delete'
    )
    if icon:
        assert len(resp.pyquery('h3 img')) == 2
        assert len(PyQuery(resp.pyquery('.section')[0]).find('h3 img')) == 0
        assert len(PyQuery(resp.pyquery('.section')[1]).find('h3 img.application-icon')) == 1
        assert len(PyQuery(resp.pyquery('.section')[2]).find('h3 img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3 img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Criterias outside applications' in resp

    # check application view
    resp = resp.click(application.name)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img.application-logo')) == 1
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 2
    assert len(resp.pyquery('.section h3')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('h3').text()
        == 'CriteriaCategory 2 [criteriacategory-2] Export Delete'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('h3').text()
        == 'CriteriaCategory 3 [criteriacategory-3] Export Delete'
    )
    assert len(resp.pyquery('h3 img')) == 0

    # check elements outside applications
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click('Criterias outside applications')
    assert resp.pyquery('h2').text() == 'Criterias outside applications'
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section h3')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('h3').text()
        == 'CriteriaCategory 1 [criteriacategory-1] Export Delete'
    )

    # check visible flag
    application.visible = False
    application.save()
    resp = app.get('/manage/pricing/criterias/')
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('ul.objects-list img')) == 0
    app.get('/manage/pricing/criterias/?application=%s' % application.slug, status=404)


@pytest.mark.parametrize('icon', [True, False])
def test_regie(app, admin_user, application_with_icon, application_without_icon, icon):
    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    regie1 = Regie.objects.create(label='Regie 1')
    regie2 = Regie.objects.create(label='Regie 2')
    ApplicationElement.objects.create(content_object=regie2, application=application)
    regie3 = Regie.objects.create(label='Regie 3')
    ApplicationElement.objects.create(content_object=regie3, application=application)

    app = login(app)

    resp = app.get('/manage/invoicing/regies/')
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'Regie 1 [identifier: regie-1]'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'Regie 2 [identifier: regie-2]'
    assert resp.pyquery('ul.objects-list li:nth-child(3)').text() == 'Regie 3 [identifier: regie-3]'
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert len(resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon')) == 1
        assert len(resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon')) == 1
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Regies outside applications' in resp

    # check application view
    resp = resp.click(application.name)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img.application-logo')) == 1
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'Regie 2 [identifier: regie-2]'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'Regie 3 [identifier: regie-3]'
    assert len(resp.pyquery('ul.objects-list li img')) == 0

    # check elements outside applications
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Regies outside applications')
    assert resp.pyquery('h2').text() == 'Regies outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'Regie 1 [identifier: regie-1]'

    # check detail page
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie1.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie2.pk)
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon')) == 1
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check visible flag
    application.visible = False
    application.save()
    resp = app.get('/manage/invoicing/regies/')
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('ul.objects-list img')) == 0
    app.get('/manage/invoicing/regies/?application=%s' % application.slug, status=404)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie2.pk)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
