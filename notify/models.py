from django.db import models


class TelegramSettings(models.Model):
    """Реквизиты Telegram-бота для уведомлений о новых заказах.

    Синглтон (всегда одна запись, pk=1), редактируется владельцем в админке.
    Токен выдаёт @BotFather. Получателей (чатов, куда слать уведомления)
    может быть несколько — см. TelegramRecipient.
    """

    is_enabled = models.BooleanField(
        'Отправлять уведомления', default=False,
        help_text='Выключите, чтобы временно остановить уведомления, не стирая токен.',
    )
    bot_token = models.CharField(
        'Токен бота', max_length=100, blank=True,
        help_text='Выдаёт @BotFather в Telegram после команды /newbot.',
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
        return bool(self.is_enabled and self.bot_token and TelegramRecipient.objects.exists())


class TelegramRecipient(models.Model):
    """Чат, куда бот шлёт уведомления. Их может быть несколько — например,
    личный чат владельца и отдельная группа менеджеров."""

    # У TelegramSettings всегда один-единственный ряд (pk=1) — FK здесь
    # только для того, чтобы получатели редактировались инлайном на его
    # странице в админке; explicit-значение можно не указывать нигде.
    settings = models.ForeignKey(
        TelegramSettings, verbose_name='Настройки', related_name='recipients',
        on_delete=models.CASCADE, editable=False, default=1,
    )
    chat_id = models.CharField(
        'Chat ID', max_length=64, unique=True,
        help_text='Можно найти автоматически кнопкой на странице настроек.',
    )
    label = models.CharField(
        'Название (для себя)', max_length=100, blank=True,
        help_text='Например «Директор» или «Чат продавцов» — чтобы не путать получателей.',
    )
    created_at = models.DateTimeField('Добавлен', auto_now_add=True)

    class Meta:
        verbose_name = 'Получатель уведомлений'
        verbose_name_plural = 'Получатели уведомлений'
        ordering = ['label', 'chat_id']

    def __str__(self):
        return self.label or self.chat_id
