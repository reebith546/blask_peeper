from django.db import models


class TelegramSettings(models.Model):
    """Реквизиты Telegram-бота для уведомлений о новых заказах.

    Синглтон (всегда одна запись, pk=1), редактируется владельцем в админке.
    Токен выдаёт @BotFather, chat_id — либо кнопкой в админке (после того как
    боту написали хотя бы одно сообщение), либо вручную.
    """

    is_enabled = models.BooleanField(
        'Отправлять уведомления', default=False,
        help_text='Выключите, чтобы временно остановить уведомления, не стирая токен.',
    )
    bot_token = models.CharField(
        'Токен бота', max_length=100, blank=True,
        help_text='Выдаёт @BotFather в Telegram после команды /newbot.',
    )
    chat_id = models.CharField(
        'Chat ID', max_length=64, blank=True,
        help_text='Куда слать уведомления — чат с ботом или группа. Можно найти кнопкой ниже.',
    )
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Настройки Telegram-уведомлений'
        verbose_name_plural = 'Настройки Telegram-уведомлений'

    def __str__(self):
        return 'Настройки Telegram-уведомлений'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_ready(self):
        return bool(self.is_enabled and self.bot_token and self.chat_id)
