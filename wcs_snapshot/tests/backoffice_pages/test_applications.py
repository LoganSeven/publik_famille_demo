import os

import pytest
from pyquery import PyQuery

from wcs.applications import Application, ApplicationElement
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    ApplicationElement.wipe()
    Application.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def application_with_icon(pub):
    application = Application()
    application.name = 'App 1'
    application.slug = 'app-1'
    application.icon = PicklableUpload('icon.png', 'image/png')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_data = fd.read()
    application.icon.receive([image_data])
    application.version_number = '1'
    application.store()
    return application


@pytest.fixture
def application_without_icon(pub):
    application = Application()
    application.name = 'App 2'
    application.slug = 'app-2'
    application.version_number = '1'
    application.store()
    return application


@pytest.mark.parametrize('icon', [True, False])
def test_formdefs(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    FormDef.wipe()
    Category.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    formdef1 = FormDef()
    formdef1.name = 'form1'
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'form2'
    formdef2.store()
    ApplicationElement.update_or_create_for_object(application, formdef2)

    formdef3 = FormDef()
    formdef3.name = 'form3'
    formdef3.store()
    ApplicationElement.update_or_create_for_object(application, formdef3)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/forms/')
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section h2')) == 0
    assert len(resp.pyquery('.section ul.objects-list li')) == 3
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'form1'
    assert resp.pyquery('.section ul.objects-list li:nth-child(2)').text() == 'form2'
    assert resp.pyquery('.section ul.objects-list li:nth-child(3)').text() == 'form3'
    if icon:
        assert len(resp.pyquery('.section ul.objects-list img')) == 2
        assert len(resp.pyquery('.section ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('.section ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('.section ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('.section ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Forms outside applications' in resp

    # check application view
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section ul.objects-list li')) == 2
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'form2'
    assert resp.pyquery('.section ul.objects-list li:nth-child(2)').text() == 'form3'
    assert len(resp.pyquery('.section ul.objects-list img')) == 0

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Forms outside applications'
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section ul.objects-list li')) == 1
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'form1'

    # with category
    cat = Category()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    formdef2.category = cat
    formdef2.store()
    resp = app.get('/backoffice/forms/')
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li').text() == 'form2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'form1'
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text() == 'form3'
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'form2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'form3'

    # check categories
    cat2 = Category()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = Category()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/forms/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check detail page
    resp = app.get('/backoffice/forms/%s/' % formdef1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/forms/%s/' % formdef2.id,
        '/backoffice/forms/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/forms/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/forms/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_carddefs(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    CardDef.wipe()
    CardDefCategory.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    carddef1 = CardDef()
    carddef1.name = 'card1'
    carddef1.store()

    carddef2 = CardDef()
    carddef2.name = 'card2'
    carddef2.store()
    ApplicationElement.update_or_create_for_object(application, carddef2)

    carddef3 = CardDef()
    carddef3.name = 'card3'
    carddef3.store()
    ApplicationElement.update_or_create_for_object(application, carddef3)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/cards/')
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section h2')) == 0
    assert len(resp.pyquery('.section ul.objects-list li')) == 3
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'card1'
    assert resp.pyquery('.section ul.objects-list li:nth-child(2)').text() == 'card2'
    assert resp.pyquery('.section ul.objects-list li:nth-child(3)').text() == 'card3'
    if icon:
        assert len(resp.pyquery('.section ul.objects-list img')) == 2
        assert len(resp.pyquery('.section ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('.section ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('.section ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('.section ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Card models outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section ul.objects-list li')) == 2
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'card2'
    assert resp.pyquery('.section ul.objects-list li:nth-child(2)').text() == 'card3'
    assert len(resp.pyquery('.section ul.objects-list img')) == 0

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Card models outside applications'
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section ul.objects-list li')) == 1
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'card1'

    # with category
    cat = CardDefCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    carddef2.category = cat
    carddef2.store()
    resp = app.get('/backoffice/cards/')
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li').text() == 'card2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'card1'
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text() == 'card3'
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'card2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'card3'

    # check detail page
    resp = app.get('/backoffice/cards/%s/' % carddef1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/cards/%s/' % carddef2.id,
        '/backoffice/cards/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = CardDefCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = CardDefCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/cards/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/cards/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/cards/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/cards/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/cards/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_workflows(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    Workflow.wipe()
    WorkflowCategory.wipe()
    FormDef.wipe()
    CardDef.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    workflow1 = Workflow()
    workflow1.name = 'workflow1'
    workflow1.store()

    workflow2 = Workflow()
    workflow2.name = 'workflow2'
    workflow2.store()
    ApplicationElement.update_or_create_for_object(application, workflow2)

    workflow3 = Workflow()
    workflow3.name = 'workflow3'
    workflow3.store()
    ApplicationElement.update_or_create_for_object(application, workflow3)

    formdef = FormDef()
    formdef.name = 'form'
    formdef.workflow = workflow2
    formdef.store()
    carddef = CardDef()
    carddef.name = 'card'
    carddef.workflow = workflow2
    carddef.store()

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/workflows/')
    assert len(resp.pyquery('.section')) == 2
    assert len(PyQuery(resp.pyquery('.section')[0]).find('h2')) == 0
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'Default Forms'
    )
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(2)').text()
        == 'Default (cards) Card models'
    )
    assert len(PyQuery(resp.pyquery('.section')[1]).find('h2')) == 0
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 3
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'workflow2 Forms and card models'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text()
        == 'workflow1 Unused'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(3)').text()
        == 'workflow3 Unused'
    )
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 0
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 2
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(3) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 0
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Workflows outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('.section')) == 1
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'workflow2 Forms and card models'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'workflow3 Unused'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Workflows outside applications'
    assert len(resp.pyquery('.section')) == 1
    assert len(resp.pyquery('.section ul.objects-list li')) == 1
    assert resp.pyquery('.section ul.objects-list li:nth-child(1)').text() == 'workflow1 Unused'

    # with category
    cat = WorkflowCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    workflow2.category = cat
    workflow2.store()
    resp = app.get('/backoffice/workflows/')
    assert len(resp.pyquery('.section')) == 3
    assert len(PyQuery(resp.pyquery('.section')[0]).find('h2')) == 0
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'Default Forms'
    )
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(2)').text()
        == 'Default (cards) Card models'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'workflow2 Forms and card models'
    )
    assert PyQuery(resp.pyquery('.section')[2]).find('h2').text() == 'Uncategorised'
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text()
        == 'workflow1 Unused'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(2)').text()
        == 'workflow3 Unused'
    )
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 0
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[2])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'workflow2 Forms and card models'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Uncategorised'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'workflow3 Unused'
    )

    # check detail page
    resp = app.get('/backoffice/workflows/%s/' % workflow1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/workflows/%s/' % workflow2.id,
        '/backoffice/workflows/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = WorkflowCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = WorkflowCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/workflows/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/forms/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/forms/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_blockdefs(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    BlockDef.wipe()
    BlockCategory.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    blockdef1 = BlockDef()
    blockdef1.name = 'block1'
    blockdef1.store()

    blockdef2 = BlockDef()
    blockdef2.name = 'block2'
    blockdef2.store()
    ApplicationElement.update_or_create_for_object(application, blockdef2)

    blockdef3 = BlockDef()
    blockdef3.name = 'block3'
    blockdef3.store()
    ApplicationElement.update_or_create_for_object(application, blockdef3)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/forms/blocks/')
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'block1'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'block2'
    assert resp.pyquery('ul.objects-list li:nth-child(3)').text() == 'block3'
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Blocks of fields outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'block2'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'block3'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Blocks of fields outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'block1'

    # with category
    cat = BlockCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    blockdef2.category = cat
    blockdef2.store()
    resp = app.get('/backoffice/forms/blocks/')
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'block2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'block1'
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text() == 'block3'
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'block2'
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'block3'

    # check detail page
    resp = app.get('/backoffice/forms/blocks/%s/' % blockdef1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/forms/blocks/%s/' % blockdef2.id,
        '/backoffice/forms/blocks/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = BlockCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = BlockCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/forms/blocks/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/forms/blocks/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/blocks/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/forms/blocks/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/blocks/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_mailtemplates(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    MailTemplate.wipe()
    MailTemplateCategory.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    mailtemplate1 = MailTemplate()
    mailtemplate1.name = 'mailtemplate1'
    mailtemplate1.store()

    mailtemplate2 = MailTemplate()
    mailtemplate2.name = 'mailtemplate2'
    mailtemplate2.store()
    ApplicationElement.update_or_create_for_object(application, mailtemplate2)

    mailtemplate3 = MailTemplate()
    mailtemplate3.name = 'mailtemplate3'
    mailtemplate3.store()
    ApplicationElement.update_or_create_for_object(application, mailtemplate3)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/workflows/mail-templates/')
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'mailtemplate1'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'mailtemplate2'
    assert resp.pyquery('ul.objects-list li:nth-child(3)').text() == 'mailtemplate3'
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Mail templates outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'mailtemplate2'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'mailtemplate3'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Mail templates outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'mailtemplate1'

    # with category
    cat = MailTemplateCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    mailtemplate2.category = cat
    mailtemplate2.store()
    resp = app.get('/backoffice/workflows/mail-templates/')
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'mailtemplate2'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'mailtemplate1'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text() == 'mailtemplate3'
    )
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text() == 'mailtemplate2'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text() == 'mailtemplate3'
    )

    # check detail page
    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mailtemplate1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/workflows/mail-templates/%s/' % mailtemplate2.id,
        '/backoffice/workflows/mail-templates/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = MailTemplateCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = MailTemplateCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/workflows/mail-templates/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/workflows/mail-templates/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/workflows/mail-templates/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/workflows/mail-templates/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/workflows/mail-templates/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_commenttemplates(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    CommentTemplate.wipe()
    CommentTemplateCategory.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    commenttemplate1 = CommentTemplate()
    commenttemplate1.name = 'commenttemplate1'
    commenttemplate1.store()

    commenttemplate2 = CommentTemplate()
    commenttemplate2.name = 'commenttemplate2'
    commenttemplate2.store()
    ApplicationElement.update_or_create_for_object(application, commenttemplate2)

    commenttemplate3 = CommentTemplate()
    commenttemplate3.name = 'commenttemplate3'
    commenttemplate3.store()
    ApplicationElement.update_or_create_for_object(application, commenttemplate3)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/workflows/comment-templates/')
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'commenttemplate1'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'commenttemplate2'
    assert resp.pyquery('ul.objects-list li:nth-child(3)').text() == 'commenttemplate3'
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Comment templates outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'commenttemplate2'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'commenttemplate3'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Comment templates outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'commenttemplate1'

    # with category
    cat = CommentTemplateCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    commenttemplate2.category = cat
    commenttemplate2.store()
    resp = app.get('/backoffice/workflows/comment-templates/')
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'commenttemplate2'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'commenttemplate1'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text()
        == 'commenttemplate3'
    )
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(1) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 2
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'commenttemplate2'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Misc'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'commenttemplate3'
    )

    # check detail page
    resp = app.get('/backoffice/workflows/comment-templates/%s/' % commenttemplate1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/workflows/comment-templates/%s/' % commenttemplate2.id,
        '/backoffice/workflows/comment-templates/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = CommentTemplateCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = CommentTemplateCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/workflows/comment-templates/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/workflows/comment-templates/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/workflows/comment-templates/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/workflows/comment-templates/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/workflows/comment-templates/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_datasources(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    NamedDataSource.wipe()
    DataSourceCategory.wipe()
    CardDef.wipe()
    CardDefCategory.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    datasource1 = NamedDataSource()
    datasource1.name = 'datasource1'
    datasource1.store()

    datasource2 = NamedDataSource()
    datasource2.name = 'datasource2'
    datasource2.store()
    ApplicationElement.update_or_create_for_object(application, datasource2)

    datasource3 = NamedDataSource()
    datasource3.name = 'datasource3'
    datasource3.store()
    ApplicationElement.update_or_create_for_object(application, datasource3)

    user_datasource1 = NamedDataSource()
    user_datasource1.name = 'user datasource1'
    user_datasource1.data_source = {'type': 'wcs:users'}
    user_datasource1.store()

    user_datasource2 = NamedDataSource()
    user_datasource2.name = 'user datasource2'
    user_datasource2.data_source = {'type': 'wcs:users'}
    user_datasource2.store()
    ApplicationElement.update_or_create_for_object(application, user_datasource2)

    carddef1 = CardDef()
    carddef1.name = 'card1'
    carddef1.digest_templates = {'default': 'foo'}
    carddef1.store()

    carddef2 = CardDef()
    carddef2.name = 'card2'
    carddef2.digest_templates = {'default': 'foo'}
    carddef2.store()
    ApplicationElement.update_or_create_for_object(application, carddef2)

    app = login(get_app(pub))

    # no categories
    resp = app.get('/backoffice/forms/data-sources/')
    assert len(resp.pyquery('.section')) == 3
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'Users Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'user datasource1 (user_datasource1)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(2)').text()
        == 'user datasource2 (user_datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Manually Configured Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 3
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource1 (datasource1)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text()
        == 'datasource2 (datasource2)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(3)').text()
        == 'datasource3 (datasource3)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured'
    )
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 2
    assert PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text() == 'card1'
    assert PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(2)').text() == 'card2'
    if icon:
        assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[0])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list img')) == 2
        assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            PyQuery(resp.pyquery('.section')[1])
            .find('ul.objects-list li:nth-child(3) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list img')) == 1
        assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            PyQuery(resp.pyquery('.section')[2])
            .find('ul.objects-list li:nth-child(2) img.application-icon')
            .attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('.section ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Data sources outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('#appbar h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('.section')) == 3
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'Users Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'user datasource2 (user_datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Manually Configured Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource2 (datasource2)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(2)').text()
        == 'datasource3 (datasource3)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured'
    )
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text() == 'card2'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('#appbar h2').text() == 'Data sources outside applications'
    assert len(resp.pyquery('.section')) == 3
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'Users Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'user datasource1 (user_datasource1)'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'Manually Configured Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource1 (datasource1)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured'
    )
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text() == 'card1'

    # with category
    cat = DataSourceCategory()
    cat.name = 'cat'
    cat.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    datasource2.category = cat
    datasource2.store()
    cat2 = CardDefCategory()
    cat2.name = 'card cat'
    cat2.store()
    ApplicationElement.update_or_create_for_object(application, cat)
    carddef2.category = cat2
    carddef2.store()
    resp = app.get('/backoffice/forms/data-sources/')
    assert len(resp.pyquery('.section')) == 5
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'Users Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'user datasource1 (user_datasource1)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(2)').text()
        == 'user datasource2 (user_datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource2 (datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[2]).find('h2').text() == 'Uncategorised'
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 2
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource1 (datasource1)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(2)').text()
        == 'datasource3 (datasource3)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[3]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured - card cat'
    )
    assert len(PyQuery(resp.pyquery('.section')[3]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[3]).find('ul.objects-list li:nth-child(1)').text() == 'card2'
    assert (
        PyQuery(resp.pyquery('.section')[4]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured - Uncategorised'
    )
    assert len(PyQuery(resp.pyquery('.section')[4]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[4]).find('ul.objects-list li:nth-child(1)').text() == 'card1'

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert len(resp.pyquery('.section')) == 4
    assert PyQuery(resp.pyquery('.section')[0]).find('h2').text() == 'Users Data Sources'
    assert len(PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[0]).find('ul.objects-list li:nth-child(1)').text()
        == 'user datasource2 (user_datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[1]).find('h2').text() == 'cat'
    assert len(PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[1]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource2 (datasource2)'
    )
    assert PyQuery(resp.pyquery('.section')[2]).find('h2').text() == 'Uncategorised'
    assert len(PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li')) == 1
    assert (
        PyQuery(resp.pyquery('.section')[2]).find('ul.objects-list li:nth-child(1)').text()
        == 'datasource3 (datasource3)'
    )
    assert (
        PyQuery(resp.pyquery('.section')[3]).find('h2').text()
        == 'Data Sources from Card Models - automatically configured - card cat'
    )
    assert len(PyQuery(resp.pyquery('.section')[3]).find('ul.objects-list li')) == 1
    assert PyQuery(resp.pyquery('.section')[3]).find('ul.objects-list li:nth-child(1)').text() == 'card2'

    # check detail page
    resp = app.get('/backoffice/forms/data-sources/%s/' % datasource1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    urls = [
        '/backoffice/forms/data-sources/%s/' % datasource2.id,
        '/backoffice/forms/data-sources/categories/%s/' % cat.id,
    ]
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
        assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
        if icon:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
            assert (
                resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr[
                    'src'
                ]
                == '../application/%s/icon' % application.slug
            )
        else:
            assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check categories
    cat2 = DataSourceCategory()
    cat2.name = 'cat2'
    cat2.store()
    cat3 = DataSourceCategory()
    cat3.name = 'cat3'
    cat3.store()
    resp = app.get('/backoffice/forms/data-sources/categories/')
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    assert len(resp.pyquery('ul.biglist li')) == 3
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    assert len(resp.pyquery('ul.objects-list li')) == 1

    # check categories outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Categories outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 2

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/forms/data-sources/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/data-sources/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/forms/data-sources/categories/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/forms/data-sources/categories/application/%s/' % application.id, status=404)
    for url in urls:
        resp = app.get(url)
        assert len(resp.pyquery('h3:contains("Applications")')) == 0
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


@pytest.mark.parametrize('icon', [True, False])
def test_wscalls(pub, application_with_icon, application_without_icon, icon):
    create_superuser(pub)

    NamedWsCall.wipe()

    if icon:
        application = application_with_icon
    else:
        application = application_without_icon

    wscall1 = NamedWsCall()
    wscall1.name = 'wscall1'
    wscall1.store()

    wscall2 = NamedWsCall()
    wscall2.name = 'wscall2'
    wscall2.store()
    ApplicationElement.update_or_create_for_object(application, wscall2)

    wscall3 = NamedWsCall()
    wscall3.name = 'wscall3'
    wscall3.store()
    ApplicationElement.update_or_create_for_object(application, wscall3)

    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/wscalls/')
    assert len(resp.pyquery('ul.objects-list li')) == 3
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'wscall1 (wscall1)'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'wscall2 (wscall2)'
    assert resp.pyquery('ul.objects-list li:nth-child(3)').text() == 'wscall3 (wscall3)'
    if icon:
        assert len(resp.pyquery('ul.objects-list img')) == 2
        assert len(resp.pyquery('ul.objects-list li:nth-child(1) img')) == 0
        assert (
            resp.pyquery('ul.objects-list li:nth-child(2) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
        assert (
            resp.pyquery('ul.objects-list li:nth-child(3) img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('ul.objects-list img')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == 'application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0
    assert 'Webservice calls outside applications' in resp

    # check application view
    resp = resp.click(href='application/%s/' % application.slug)
    assert resp.pyquery('h2').text() == application.name
    if icon:
        assert len(resp.pyquery('h2 img')) == 1
        assert resp.pyquery('h2 img.application-logo').attr['src'] == 'logo'
    else:
        assert len(resp.pyquery('h2 img')) == 0
    assert len(resp.pyquery('ul.objects-list li')) == 2
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'wscall2 (wscall2)'
    assert resp.pyquery('ul.objects-list li:nth-child(2)').text() == 'wscall3 (wscall3)'

    # check elements outside applications
    resp = resp.click(href='application/')
    assert resp.pyquery('h2').text() == 'Webservice calls outside applications'
    assert len(resp.pyquery('ul.objects-list li')) == 1
    assert resp.pyquery('ul.objects-list li:nth-child(1)').text() == 'wscall1 (wscall1)'

    # check detail page
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall1.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall2.id)
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph + .button-paragraph')) == 0
    assert resp.pyquery('h3:contains("Applications") + .button-paragraph').text() == application.name
    if icon:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 1
        assert (
            resp.pyquery('h3:contains("Applications") + .button-paragraph img.application-icon').attr['src']
            == '../application/%s/icon' % application.slug
        )
    else:
        assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph img')) == 0

    # check visible flag
    application = Application.get(application.id)
    application.visible = False
    application.store()
    resp = app.get('/backoffice/settings/wscalls/')
    assert len(resp.pyquery('.extra-actions-menu li')) == 0
    app.get('/backoffice/settings/wscalls/application/%s/' % application.id, status=404)
    assert len(resp.pyquery('img.application-icon')) == 0
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall2.id)
    assert len(resp.pyquery('h3:contains("Applications")')) == 0
    assert len(resp.pyquery('h3:contains("Applications") + .button-paragraph')) == 0


def test_object_applications(pub, application_without_icon):
    application = application_without_icon

    FormDef.wipe()
    Category.wipe()
    CardDef.wipe()
    CardDefCategory.wipe()
    Workflow.wipe()
    WorkflowCategory.wipe()
    BlockDef.wipe()
    BlockCategory.wipe()
    MailTemplate.wipe()
    MailTemplateCategory.wipe()
    CommentTemplate.wipe()
    CommentTemplateCategory.wipe()
    NamedDataSource.wipe()
    DataSourceCategory.wipe()
    NamedWsCall.wipe()

    objects = []

    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()
    workflow = Workflow(name='test')
    workflow.store()
    ApplicationElement.update_or_create_for_object(application, workflow_category)
    ApplicationElement.update_or_create_for_object(application, workflow)
    objects += [workflow_category, workflow]

    blockdef_category = BlockCategory(name='test')
    blockdef_category.store()
    blockdef = BlockDef(name='test')
    blockdef.store()
    ApplicationElement.update_or_create_for_object(application, blockdef_category)
    ApplicationElement.update_or_create_for_object(application, blockdef)
    objects += [blockdef_category, blockdef]

    formdef_category = Category(name='Test')
    formdef_category.store()
    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()
    ApplicationElement.update_or_create_for_object(application, formdef_category)
    ApplicationElement.update_or_create_for_object(application, formdef)
    objects += [formdef_category, formdef]

    carddef_category = CardDefCategory(name='Test')
    carddef_category.store()
    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()
    ApplicationElement.update_or_create_for_object(application, carddef_category)
    ApplicationElement.update_or_create_for_object(application, carddef)
    objects += [carddef_category, carddef]

    datasource_category = DataSourceCategory(name='Test')
    datasource_category.store()
    datasource = NamedDataSource(name='Test')
    datasource.store()
    ApplicationElement.update_or_create_for_object(application, datasource_category)
    ApplicationElement.update_or_create_for_object(application, datasource)
    objects += [datasource_category, datasource]

    mailtemplate_category = MailTemplateCategory(name='Test')
    mailtemplate_category.store()
    mailtemplate = MailTemplate(name='Test')
    mailtemplate.store()
    ApplicationElement.update_or_create_for_object(application, mailtemplate_category)
    ApplicationElement.update_or_create_for_object(application, mailtemplate)
    objects += [mailtemplate_category, mailtemplate]

    commenttemplate_category = CommentTemplateCategory(name='Test')
    commenttemplate_category.store()
    commenttemplate = CommentTemplate(name='Test')
    commenttemplate.store()
    ApplicationElement.update_or_create_for_object(application, commenttemplate_category)
    ApplicationElement.update_or_create_for_object(application, commenttemplate)
    objects += [commenttemplate_category, commenttemplate]

    wscall = NamedWsCall(name='Test')
    wscall.store()
    ApplicationElement.update_or_create_for_object(application, wscall)
    objects += [wscall]

    assert ApplicationElement.count() == 15

    for obj in objects:
        assert len(obj.applications) == 1
        obj.store()
        obj = obj.__class__.get(obj.id)
        assert '_applications' not in obj.__dict__


def test_workflow_edit_slug(pub, application_without_icon):
    create_superuser(pub)
    application = application_without_icon
    Workflow.wipe()

    workflow = Workflow(name='test')
    workflow.store()
    ApplicationElement.update_or_create_for_object(application, workflow)

    app = login(get_app(pub))
    resp = app.get(workflow.get_admin_url())
    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'test'
    assert resp.forms[0]['slug'].value == 'test'
    assert 'change-nevertheless' in resp.text


def test_workflow_delete_status(pub, application_without_icon):
    create_superuser(pub)
    application = application_without_icon
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    st3 = workflow.add_status('st3')
    workflow.store()
    ApplicationElement.update_or_create_for_object(application, workflow)

    app = login(get_app(pub))
    resp = app.get(st1.get_admin_url())
    resp = resp.click('Delete')
    resp = resp.form.submit('submit').follow()  # -> reassign
    assert 'This workflow is part of an application and may have cards/forms' in resp.text
    resp.form['action'] = f'reassign-{st2.id}'
    resp = resp.form.submit('submit').follow()

    resp = app.get(st3.get_admin_url())
    resp = resp.click('Delete')
    resp = resp.form.submit('submit').follow()  # -> reassign
    assert 'This workflow is part of an application and may have cards/forms' in resp.text
    resp.form['action'] = 'remove'
    resp = resp.form.submit('submit').follow()

    workflow.refresh_from_storage()
    assert workflow.status_remapping[str(st1.id)]['action'] == f'reassign-{st2.id}'
    assert workflow.status_remapping[str(st1.id)]['status'] == str(st1.id)
    assert workflow.status_remapping[str(st1.id)]['timestamp']
    assert workflow.status_remapping[str(st3.id)]['action'] == 'remove'
    assert workflow.status_remapping[str(st3.id)]['timestamp']
    assert workflow.status_remapping[str(st3.id)]['status'] == str(st3.id)


def test_workflow_workflow_change(pub, application_without_icon):
    create_superuser(pub)
    application = application_without_icon
    Workflow.wipe()
    FormDef.wipe()

    workflow = Workflow(name='Test')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    workflow.store()

    workflow2 = Workflow(name='Test2')
    workflow2.add_status('wf2 st0')
    wf2_st1 = workflow2.add_status('wf2 st1')
    wf2_st2 = workflow2.add_status('wf2 st2')
    workflow2.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    ApplicationElement.update_or_create_for_object(application, formdef)

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url())
    resp = resp.click(href=r'^workflow$')
    resp.form['workflow_id'] = workflow2.id
    resp = resp.form.submit('submit').follow()  # -> remap
    resp.form[f'mapping-{st1.id}'] = 'wf2 st1'
    resp.form[f'mapping-{st2.id}'] = 'wf2 st2'
    resp = resp.form.submit('submit').follow()
    formdef.refresh_from_storage()
    assert formdef.workflow_migrations['test test2']['old_workflow'] == 'test'
    assert formdef.workflow_migrations['test test2']['new_workflow'] == 'test2'
    assert formdef.workflow_migrations['test test2']['status_mapping'] == {
        st1.id: wf2_st1.id,
        st2.id: wf2_st2.id,
    }
