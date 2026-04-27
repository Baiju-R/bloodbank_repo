from django.apps import AppConfig


class DonorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'donor'

    def ready(self):  # pragma: no cover - import side-effects
        from . import signals  # noqa: F401
