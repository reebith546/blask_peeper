from django import template

register = template.Library()


@register.filter
def kzt(value):
    """Форматирует сумму в тенге: 18500 -> «18 500 ₸»."""
    try:
        amount = int(round(float(value)))
    except (TypeError, ValueError):
        return value
    return f'{amount:,}'.replace(',', ' ') + ' ₸'


@register.filter
def stars(rating):
    """Возвращает строку из звёзд для рейтинга отзыва: 4 -> «★★★★»."""
    try:
        return '★' * int(rating)
    except (TypeError, ValueError):
        return ''
