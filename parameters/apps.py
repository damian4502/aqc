from django.apps import AppConfig


class ParametersConfig(AppConfig):
    default = True
    name = "parameters"

    def ready(self):
        from . import signals  # noqa: F401
