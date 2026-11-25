from django.apps import AppConfig
from django.db.models.signals import post_migrate


def poblar_catalogos_post_migrate(sender, **kwargs):
    try:
        from .catalog import inicializar_catalogos_bd
        inicializar_catalogos_bd()
    except Exception:
        pass


class RecepcionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'recepcion'

    def ready(self):
        post_migrate.connect(poblar_catalogos_post_migrate, sender=self)
