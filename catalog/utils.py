import random


def shuffle(array):
    """Вернуть новый список с элементами исходной последовательности в случайном порядке.

    Универсальный помощник для рандомизации выдачи товаров в каталоге:
    исходная последовательность не изменяется, всегда возвращается новый список.
    """
    items = list(array)
    random.shuffle(items)
    return items


_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya',
    # казахские буквы
    'ә': 'a', 'ғ': 'g', 'қ': 'k', 'ң': 'n', 'ө': 'o', 'ұ': 'u', 'ү': 'u', 'һ': 'h', 'і': 'i',
}


def normalize_slug(text):
    """Латиница в нижнем регистре через дефис: «Букеты_роз» -> «bukety-roz».

    Для URL это стандарт: читаемо в выдаче и мессенджерах (вместо %D0%91…),
    «_» поисковики не считают разделителем, а регистр не плодит дубли.
    Уже нормальный slug возвращается без изменений.
    """
    import re

    text = (text or '').strip().lower()
    translit = ''.join(_TRANSLIT.get(ch, ch) for ch in text)
    translit = re.sub(r'[^a-z0-9]+', '-', translit)
    return translit.strip('-')


def unique_slug(model, slug, exclude_pk=None):
    """Добавляет -2, -3… если такой slug уже занят другой записью."""
    base = slug or 'item'
    candidate, n = base, 2
    qs = model._default_manager.all()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=candidate).exists():
        candidate = f'{base}-{n}'
        n += 1
    return candidate
