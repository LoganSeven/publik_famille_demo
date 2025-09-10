import os
import shlex
from pathlib import Path

import nox

nox.options.reuse_venv = True

DJANGO_VERSIONS = ['>=4.2,<4.3']


def run_hook(name, *args, **kwargs):
    for file in [Path(__name__).parent / '.nox-hooks.py', Path('~/.config/nox/eo-hooks.py').expanduser()]:
        if not file.exists():
            continue

        globals_ = {}
        exec(file.read_text(), globals_)  # noqa pylint: disable=exec-used
        hook = globals_.get(name, None)
        if hook:
            hook(*args, **kwargs)


def get_lasso3(session):
    src_dir = Path('/usr/lib/python3/dist-packages/')
    venv_dir = Path(session.virtualenv.location)
    for dst_dir in venv_dir.glob('lib/**/site-packages'):
        files_to_link = [src_dir / 'lasso.py'] + list(src_dir.glob('_lasso.cpython-*.so'))

        for src_file in files_to_link:
            dst_file = dst_dir / src_file.name
            if dst_file.exists():
                dst_file.unlink()
            session.log('%s => %s', dst_file, src_file)
            dst_file.symlink_to(src_file)


def setup_venv(session, *packages, django_version=DJANGO_VERSIONS[0]):
    packages = [
        f'django{django_version}',
        'pytest>=3.6',
        'WebTest',
        'responses',
        'pyzbar',
        'schwifty',
        'mechanize',
        *packages,
    ]
    run_hook('setup_venv', session, packages)
    session.install('-e', '.', *packages, silent=False)
    get_lasso3(session)


def hookable_run(session, *args, **kwargs):
    args = list(args)
    run_hook('run', session, args, kwargs)
    session.run(*args, **kwargs)


@nox.session
@nox.parametrize('django', DJANGO_VERSIONS)
def tests(session, django):
    setup_venv(
        session,
        'astroid!=2.5.7',
        'bleach[css]>=5.0,<6',
        'mock',
        'pyquery',
        'requests',
        'pytest-cov',
        'pytest-django',
        'pytest-freezer',
        'pytest-xdist',
        'diff-cover',
        django_version=django,
    )

    session.run('python', 'manage.py', 'compilemessages', silent=True)

    args = ['py.test']
    coverage = False
    if '--coverage' in session.posargs or not session.interactive:
        coverage = True
        while '--coverage' in session.posargs:
            session.posargs.remove('--coverage')
        args += [
            '--cov-report',
            'xml:coverage.xml',
            '--cov-report',
            'html:htmlcov',
            '--cov-context',
            'test',
            '--cov=.',
            '--cov-config',
            '.coveragerc',
            '-v',
            f'--junitxml=junit-coverage.django-{django}.xml',
        ]

    args += ['--dist', 'loadfile']
    if not session.interactive:
        args += ['-v', '--numprocesses', '8']

    args += session.posargs + ['tests/']

    hookable_run(
        session,
        *args,
        env={
            'LANG': 'C',
            'LC_ALL': 'C',
            'LC_TIME': 'C',
            'DJANGO_SETTINGS_MODULE': 'wcs.settings',
            'WCS_SETTINGS_FILE': 'tests/settings.py',
            'SETUPTOOLS_USE_DISTUTILS': 'stdlib',
        },
    )

    if coverage:
        if not os.path.isdir('diff-cover'):
            os.mkdir('diff-cover')
        diff_cover_cmd = shlex.join(
            [
                'diff-cover',
                'coverage.xml',
                '--format',
                'html:diff-cover/diff-cover.html',
                '--external-css-file',
                'diff-cover/diff-cover.css',
                '--fail-under',
                '100',
            ],
        )
        diff_cover_status_json = 'diff_cover_status.json'
        session.run(
            '/bin/sh',
            '-c',
            '{ %s && echo -n 0 >&3 || { echo -n 1 >&3; false; }; } 3> %s'
            % (diff_cover_cmd, diff_cover_status_json),
            external=True,
        )


@nox.session
def pylint(session):
    setup_venv(session, 'pylint', 'pylint-django', 'nox')
    pylint_command = ['pylint', '--jobs', '6', '-f', 'parseable', '--rcfile', 'pylint.rc']

    if not session.posargs:
        pylint_command += ['wcs/', 'tests/', 'noxfile.py']
    else:
        pylint_command += session.posargs

    if not session.interactive:
        session.run(
            'bash',
            '-c',
            f'{shlex.join(pylint_command)} | tee pylint.out ; test $PIPESTATUS -eq 0',
            external=True,
        )
    else:
        session.run(*pylint_command)


@nox.session
def codestyle(session):
    session.install('pre-commit')
    session.run('pre-commit', 'run', '--all-files', '--show-diff-on-failure')


@nox.session
def check_manifest(session):
    # django is only required to compile messages
    session.install('django', 'check-manifest')
    # compile messages and css
    ignores = [
        'VERSION',
        'data/qommon',
        'wcs/qommon/static/css/*.css',
        'wcs/qommon/static/css/*.css.map',
        'wcs/qommon/static/css/dc2/*.css',
        'wcs/qommon/static/css/dc2/*.css.map',
        'merge-junit-results.py',
    ]
    session.run('check-manifest', '--ignore', ','.join(ignores))
