#! /usr/bin/env python3

import os
import shutil
import subprocess
import sys

try:
    from setuptools import Command
    from setuptools.command.build import build as _build
    from setuptools.errors import CompileError
except ImportError:
    from distutils.cmd import Command
    from distutils.command.build import build as _build
    from distutils.errors import CompileError

from setuptools import find_packages, setup
from setuptools.command.install_lib import install_lib as _install_lib
from setuptools.command.sdist import sdist as _sdist

local_cfg = None
if os.path.exists('wcs/wcs_cfg.py'):
    local_cfg = open('wcs/wcs_cfg.py').read()
    os.unlink('wcs/wcs_cfg.py')


class compile_translations(Command):
    description = 'compile message catalogs to MO files via django compilemessages'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            os.environ.pop('DJANGO_SETTINGS_MODULE', None)
            from django.core.management import call_command

            for path, dirs, files in os.walk('wcs'):
                if 'locale' not in dirs:
                    continue
                curdir = os.getcwd()
                os.chdir(os.path.realpath(path))
                call_command('compilemessages')
                os.chdir(curdir)
        except ImportError:
            sys.stderr.write('!!! Please install Django >= 1.4 to build translations\n')


class compile_scss(Command):
    description = 'compile scss files into css files'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        sass_bin = shutil.which('sassc')
        if not sass_bin:
            raise CompileError('sassc is required but was not found.')

        for path, dirnames, filenames in os.walk('wcs'):
            for filename in filenames:
                if not filename.endswith('.scss'):
                    continue
                if filename.startswith('_'):
                    continue
                subprocess.check_call(
                    [
                        sass_bin,
                        '--sourcemap',
                        '%s/%s' % (path, filename),
                        '%s/%s' % (path, filename.replace('.scss', '.css')),
                    ]
                )


class build(_build):
    sub_commands = [('compile_translations', None), ('compile_scss', None)] + _build.sub_commands


class install_lib(_install_lib):
    def run(self):
        self.run_command('compile_translations')
        _install_lib.run(self)


class eo_sdist(_sdist):
    def run(self):
        if os.path.exists('VERSION'):
            os.remove('VERSION')
        version = get_version()
        version_file = open('VERSION', 'w')
        version_file.write(version)
        version_file.close()
        _sdist.run(self)
        if os.path.exists('VERSION'):
            os.remove('VERSION')


def data_tree(destdir, sourcedir):
    extensions = [
        '.css',
        '.png',
        '.jpeg',
        '.jpg',
        '.gif',
        '.xml',
        '.html',
        '.js',
        '.ezt',
        '.dat',
        '.eot',
        '.svg',
        '.ttf',
        '.woff',
        '.scss',
        '.map',
    ]
    r = []
    for root, dirs, files in os.walk(sourcedir):
        l = [os.path.join(root, x) for x in files if os.path.splitext(x)[1] in extensions]
        r.append((root.replace(sourcedir, destdir, 1), l))
        for vcs_dirname in ('CVS', '.svn', '.bzr', '.git'):
            if vcs_dirname in dirs:
                dirs.remove(vcs_dirname)
    return r


def get_version():
    """Use the VERSION, if absent generates a version with git describe, if not
    tag exists, take 0.0- and add the length of the commit log.
    """
    if os.path.exists('VERSION'):
        with open('VERSION') as v:
            return v.read()
    if os.path.exists('.git'):
        p = subprocess.Popen(
            ['git', 'describe', '--dirty=.dirty', '--match=v*'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result = p.communicate()[0]
        if p.returncode == 0:
            result = result.decode('ascii').strip()[1:]  # strip spaces/newlines and initial v
            if '-' in result:  # not a tagged version
                real_number, commit_count, commit_hash = result.split('-', 2)
                version = '%s.post%s+%s' % (real_number, commit_count, commit_hash)
            else:
                version = result.replace('.dirty', '+dirty')
            return version
        else:
            return '0.0.post%s' % len(subprocess.check_output(['git', 'rev-list', 'HEAD']).splitlines())
    return '0.0'


cmdclass = {
    'build': build,
    'compile_scss': compile_scss,
    'compile_translations': compile_translations,
    'install_lib': install_lib,
    'sdist': eo_sdist,
}

setup(
    name='wcs',
    version=get_version(),
    maintainer='Frederic Peters',
    maintainer_email='fpeters@entrouvert.com',
    url='http://wcs.labs.libre-entreprise.org',
    install_requires=[
        'Quixote>=3.0',
        'django>=3.2',
        'psycopg2',
        'bleach[css]>=5.0',
        'dnspython',
        'gadjo>=0.53',
        'django-ckeditor<4.5.4',
        'django-ratelimit<3',
        'XStatic-Leaflet',
        'XStatic-Leaflet-GestureHandling',
        'XStatic-Select2',
        'pyproj',
        'pyquery',
        'unidecode',
        'lxml',
        'vobject',
        'qrcode',
        'Pillow',
        'gadjo',
        'docutils',
        'django-ckeditor@git+https://git.entrouvert.org/entrouvert/debian-django-ckeditor.git',
        'XStatic-godo@git+https://git.entrouvert.org/entrouvert/godo.js.git',
        'XStatic-Mapbox-GL-Leaflet@git+https://git.entrouvert.org/entrouvert/xstatic-mapbox-gl-leaflet.git',
        'langdetect',
        'python-magic',
        'workalendar',
        'requests',
        'setproctitle',
        'phonenumbers',
        'emoji',
        'psutil',
        'freezegun',
    ],
    package_dir={'wcs': 'wcs'},
    packages=find_packages(),
    cmdclass=cmdclass,
    scripts=['manage.py'],
    include_package_data=True,
    data_files=data_tree('share/wcs/web/', 'data/web/')
    + data_tree('share/wcs/themes/', 'data/themes/')
    + data_tree('share/wcs/vendor/', 'data/vendor/')
    + data_tree('share/wcs/qommon/', 'wcs/qommon/static/'),
)

if local_cfg:
    open('wcs/wcs_cfg.py', 'w').write(local_cfg)
