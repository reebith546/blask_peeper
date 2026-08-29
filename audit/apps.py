from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'audit'
    verbose_name = 'Журнал действий'

    def ready(self):
        # Подключаем приёмники сигналов входа/выхода только после полной
        # загрузки приложений — иначе часть импортов ещё недоступна.
        from . import signals  # noqa: F401
