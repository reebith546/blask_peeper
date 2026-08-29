from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEvent(models.Model):
    """Неизменяемая запись журнала действий команды.

    Пишется только на добавление (append-only): записи нельзя редактировать
    и нельзя удалять через админку — ни продавцу, ни директору, ни владельцу.
    Массовая чистка старых записей — отдельной командой ``cleanup_audit``.

    Область журнала — действия персонала в админке и аутентификация. Заказы
    и отзывы, созданные клиентами на сайте, сюда не попадают: это бизнес-данные,
    они видны в своих разделах.
    """

    class Action(models.TextChoices):
        CREATE = 'create', 'Создание'
        UPDATE = 'update', 'Изменение'
        DELETE = 'delete', 'Удаление'
        BULK_ACTION = 'bulk_action', 'Массовое действие'
        LOGIN = 'login', 'Вход'
        LOGOUT = 'logout', 'Выход'
        LOGIN_FAILED = 'login_failed', 'Неудачный вход'
        PASSWORD_CHANGE = 'password_change', 'Смена пароля'
        ACTIVATE = 'activate', 'Активация аккаунта'
        DEACTIVATE = 'deactivate', 'Деактивация аккаунта'
        EXPORT = 'export', 'Выгрузка данных'
        VIEW = 'view', 'Просмотр страницы'
        SYSTEM = 'system', 'Системное событие'

    class Role(models.TextChoices):
        OWNER = 'owner', 'Владелец'
        DIRECTOR = 'director', 'Директор'
        SELLER = 'seller', 'Продавец'
        SYSTEM = 'system', 'Система'
        ANON = 'anon', 'Не авторизован'

    class Outcome(models.TextChoices):
        SUCCESS = 'success', 'Успех'
        DENIED = 'denied', 'Отказано'
        ERROR = 'error', 'Ошибка'

    timestamp = models.DateTimeField('Время', auto_now_add=True, db_index=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='audit_events',
    )
    # Текстовый снимок — остаётся читаемым, даже если учётку потом удалят.
    actor_repr = models.CharField('Кто (снимок)', max_length=200, blank=True)
    actor_role = models.CharField('Роль', max_length=20, choices=Role.choices, db_index=True)

    action = models.CharField('Действие', max_length=20, choices=Action.choices, db_index=True)
    outcome = models.CharField('Результат', max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)

    target_content_type = models.ForeignKey(
        ContentType, verbose_name='Тип объекта', null=True, blank=True, on_delete=models.SET_NULL,
    )
    target_object_id = models.CharField('ID объекта', max_length=64, blank=True)
    target = GenericForeignKey('target_content_type', 'target_object_id')
    target_repr = models.CharField('Объект (снимок)', max_length=300, blank=True)

    # {"поле": [было, стало]}; для M2M — списки участников до/после.
    changes = models.JSONField('Изменения', null=True, blank=True)
    # Произвольный контекст: имя действия, число объектов, статус ответа и т.п.
    context = models.JSONField('Контекст', null=True, blank=True)

    ip_address = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    user_agent = models.CharField('User-Agent', max_length=400, blank=True)
    request_path = models.CharField('Путь', max_length=400, blank=True)
    request_method = models.CharField('Метод', max_length=10, blank=True)

    class Meta:
        verbose_name = 'Запись журнала'
        verbose_name_plural = 'Журнал действий'
        ordering = ['-timestamp']
        # Прав add/change/delete не существует в принципе — журнал только для чтения.
        default_permissions = ('view',)
        indexes = [
            models.Index(fields=['actor', '-timestamp']),
            models.Index(fields=['actor_role', '-timestamp']),
            models.Index(fields=['target_content_type', 'target_object_id']),
        ]

    def __str__(self):
        return f'{self.timestamp:%d.%m.%Y %H:%M} · {self.actor_repr or "—"} · {self.get_action_display()}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError('Записи журнала действий неизменяемы (append-only).')
        return super().save(*args, **kwargs)
