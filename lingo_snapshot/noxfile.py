import os
import shlex
from pathlib import Path

import nox

nox.options.reuse_venv = True


def run_hook(name, *args, **kwargs):
    for file in [Path(__name__).parent / '.nox-hooks.py', Path('~/.config/nox/eo-hooks.py').expanduser()]:
        if not file.exists():
            continue

        globals_ = {}
        exec(file.read_text(), globals_)
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


def setup_venv(session, *packages, django_version='>=4.2,<4.3'):
    packages = [
        f'django{django_version}',
        'WebTest',
        'django-webtest',
        'git+https://git.entrouvert.org/entrouvert/django-mellon.git',
        'git+https://git.entrouvert.org/entrouvert/eopayment.git',
        'git+https://git.entrouvert.org/entrouvert/debian-django-ckeditor.git',
        'git+https://git.entrouvert.org/entrouvert/publik-django-templatetags.git',
        'git+https://git.entrouvert.org/entrouvert/gadjo.git',
        'pyquery',
        'pytest!=5.3.3',
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
@nox.parametrize('django,drf', [('>=4.2,<4.3', '>=3.14,<3.15')])
def tests(session, django, drf):
    setup_venv(
        session,
        'django-filter>=2.4,<2.5',
        'psycopg2-binary',
        'pytest-cov',
        'pytest-django',
        'pytest-freezer',
        f'djangorestframework{drf}',
        'diff-cover!=9.4.0',
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
            'xml',
            '--cov-report',
            'html',
            '--cov-context=test',
            '--cov=.',
            '--cov-config',
            '.coveragerc',
            '-v',
            f'--junitxml=junit-coverage.django-{django}.xml',
        ]

    args += session.posargs + ['tests/']

    hookable_run(
        session,
        *args,
        env={
            'DJANGO_SETTINGS_MODULE': 'lingo.settings',
            'LINGO_SETTINGS_FILE': 'tests/settings.py',
            'SETUPTOOLS_USE_DISTUTILS': 'stdlib',
            'DB_ENGINE': 'django.db.backends.postgresql_psycopg2',
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
            ]
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
        pylint_command += ['lingo/', 'tests/', 'noxfile.py']
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
        'lingo/manager/static/css/style.css',
        'lingo/version.py',
        'merge-junit-results.py',
    ]
    session.run('check-manifest', '--ignore', ','.join(ignores))
