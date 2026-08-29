from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record

DEFAULT_RETENTION_DAYS = 730  # 24 месяца


class Command(BaseCommand):
    help = 'Удаляет записи журнала действий старше срока хранения (по умолчанию 730 дней / 24 месяца).'

    def add_arguments(self, parser):
        parser.add_argument('--older-than-days', type=int, default=DEFAULT_RETENTION_DAYS)
        parser.add_argument('--dry-run', action='store_true', help='Только показать, сколько удалилось бы.')

    def handle(self, *args, **options):
        days = options['older_than_days']
        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = AuditEvent.objects.filter(timestamp__lt=cutoff)
        count = qs.count()

        if options['dry_run']:
            self.stdout.write(f'dry-run: под удаление попадает {count} записей старше {cutoff:%Y-%m-%d}.')
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Удалено {count} записей журнала старше {cutoff:%Y-%m-%d} ({days} дней).'
        ))
        # Сам факт чистки тоже фиксируем — как системное событие.
        record(
            action=AuditEvent.Action.SYSTEM,
            actor_role=AuditEvent.Role.SYSTEM,
            context={'command': 'cleanup_audit', 'deleted': count, 'older_than_days': days},
        )
