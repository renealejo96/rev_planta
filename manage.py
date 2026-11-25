#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
        from django.core.management.commands.runserver import Command as RunserverCommand
        # Puerto personalizado por defecto (8420) para evitar conflictos con puertos estándar (8000/5000) en VPS/Nginx
        RunserverCommand.default_port = os.environ.get('APP_PORT', '8420')
        RunserverCommand.default_addr = os.environ.get('APP_HOST', '0.0.0.0')
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
