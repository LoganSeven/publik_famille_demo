import os

from .utilities import create_temporary_pub, get_app


def test_maintenance_page(settings):
    pub = create_temporary_pub()
    app = get_app(pub)
    resp = app.get('/')
    assert resp.status_code == 200

    site_options_path = os.path.join(pub.app_dir, 'site-options.cfg')
    with open(site_options_path, 'w') as fd:
        fd.write(
            '''\
            [variables]
            maintenance_page = True
            '''
        )
    resp = app.get('/', status=503)
    assert 'This site is currently unavailable.' in resp.text

    with open(site_options_path, 'w') as fd:
        fd.write(
            '''\
            [variables]
            maintenance_page = True
            maintenance_page_message = foo bar
            '''
        )
    resp = app.get('/', status=503)
    assert 'This site is currently unavailable.' in resp.text
    assert 'foo bar' in resp.text

    settings.MAINTENANCE_PASS_THROUGH_IPS = ['127.0.0.1']
    resp = app.get('/')
    assert resp.status_code == 200

    settings.MAINTENANCE_PASS_THROUGH_IPS = []
    resp = app.get('/', status=503)

    settings.MAINTENANCE_PASS_THROUGH_IPS = ['127.0.0.1/4']
    resp = app.get('/', status=200)

    settings.MAINTENANCE_PASS_THROUGH_IPS = []
    resp = app.get('/', status=503)

    with open(site_options_path, 'w') as fd:
        fd.write(
            '''\
            [variables]
            maintenance_page = True
            maintenance_pass_through_header = X-Entrouvert
            '''
        )
    resp = app.get('/', headers={'X-Entrouvert': 'yes'})
    assert resp.status_code == 200
