#!/usr/bin/env python
"""
Management utility for the Publik Famille Demo project.

This script provides the command-line entry point for common
Django operations such as running the development server,
applying migrations, creating superusers, etc.

Usage
-----
Run the following command for help:

    python manage.py help
"""

import os
import sys


def main():
    """
    Run administrative tasks for the Django project.

    Configures the default settings module and delegates
    command execution to Django's management utility.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "publik_famille_demo.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
