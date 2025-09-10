#! /usr/bin/env python

import glob
import itertools
import os
import re
import shutil
import subprocess
import sys

from setuptools import Command, find_packages, setup
from setuptools.command.build import build as _build
from setuptools.command.install_lib import install_lib as _install_lib
from setuptools.command.sdist import sdist
from setuptools.errors import CompileError


class eo_sdist(sdist):
    def run(self):
        if os.path.exists('VERSION'):
            os.remove('VERSION')
        version = get_version()
        with open('VERSION', 'w') as fd:
            fd.write(version)
        with open('lingo/version.py', 'w') as fd:
            fd.write('VERSION = %r\n' % version)
        sdist.run(self)
        if os.path.exists('VERSION'):
            os.remove('VERSION')


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


def data_tree(destdir, sourcedir):
    extensions = ['.css', '.png', '.jpeg', '.jpg', '.gif', '.xml', '.html', '.js']
    r = []
    for root, dirs, files in os.walk(sourcedir):
        l = [os.path.join(root, x) for x in files if os.path.splitext(x)[1] in extensions]
        r.append((root.replace(sourcedir, destdir, 1), l))
    return r


class compile_translations(Command):
    description = 'compile message catalogs to MO files via django compilemessages'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        orig_dir = os.getcwd()
        try:
            from django.core.management import call_command

            for path, dirs, files in os.walk('lingo'):
                if 'locale' not in dirs:
                    continue
                curdir = os.getcwd()
                os.chdir(os.path.realpath(path))
                call_command('compilemessages')
                os.chdir(curdir)
        except ImportError:
            sys.stderr.write('!!! Please install Django >= 1.4 to build translations\n')
        os.chdir(orig_dir)


class compile_scss(Command):
    description = 'compile scss files into css files'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        sass_bin = None
        for program in ('sassc', 'sass'):
            sass_bin = shutil.which(program)
            if sass_bin:
                break
        if not sass_bin:
            raise CompileError(
                'A sass compiler is required but none was found.  See sass-lang.com for choices.'
            )

        for path, dirnames, filenames in os.walk('lingo'):
            for filename in filenames:
                if not filename.endswith('.scss'):
                    continue
                if filename.startswith('_'):
                    continue
                subprocess.check_call(
                    [
                        sass_bin,
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


setup(
    name='lingo',
    version=get_version(),
    description='Payments and Bills System',
    author='Thomas NOËL',
    author_email='tnoel@entrouvert.com',
    packages=find_packages(exclude=['tests']),
    include_package_data=True,
    scripts=('manage.py',),
    url='https://dev.entrouvert.org/projects/lingo/',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Framework :: Django',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
    ],
    install_requires=[
        'django>=3.2, <4.3',
        'django-ckeditor<4.5.4',
        'gadjo>=0.53',
        'requests',
        'eopayment>=3.3',
        'djangorestframework>=3.3, <3.15',
        'django-filter',
        'weasyprint',
        'sorl-thumbnail<12.11.0',
    ],
    zip_safe=False,
    cmdclass={
        'build': build,
        'compile_scss': compile_scss,
        'compile_translations': compile_translations,
        'install_lib': install_lib,
        'sdist': eo_sdist,
    },
)
