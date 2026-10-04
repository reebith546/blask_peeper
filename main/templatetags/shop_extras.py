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


@register.filter
def thumb(image, spec):
    """Уменьшенная WebP-копия картинки: «600x750» — кроп по центру ровно в
    этот размер, «1000» — вписать по ширине, сохранив пропорции.

    Файлы лежат рядом с оригиналами (MEDIA_ROOT/thumbs/…) и создаются при
    первом обращении, потом отдаются из кэша на диске. При любой ошибке
    (нет файла, битое изображение) возвращаем исходный URL — страница не падает.
    """
    import hashlib
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    if not image:
        return ''
    try:
        spec = str(spec).lower()
        if 'x' in spec:
            width, height = (int(v) for v in spec.split('x'))
        else:
            width, height = int(spec), None
        storage = image.storage
        name = image.name
        key = hashlib.md5(f'{name}|{spec}|{storage.size(name)}'.encode()).hexdigest()[:16]
        thumb_name = f'thumbs/{spec}/{key}.webp'
        if not storage.exists(thumb_name):
            with storage.open(name) as fh:
                img = ImageOps.exif_transpose(Image.open(fh))
                img.load()
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            if height:
                img = ImageOps.fit(img, (width, height), Image.LANCZOS)
            elif img.width > width:
                img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, 'WEBP', quality=80, method=4)
            storage.save(thumb_name, ContentFile(buf.getvalue()))
        return storage.url(thumb_name)
    except Exception:
        return image.url
