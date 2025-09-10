#!/usr/bin/env python
# manage.py
import os, sys
def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'publik_famille_demo.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
if __name__ == '__main__':
    main()
