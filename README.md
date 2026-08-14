# Black Pepper Flower Bar — «Цветы с характером»

Интернет-магазин цветов на Django для Алматы.

## Приложения

- `catalog` — категории и товары
- `orders` — заказы и позиции заказа
- `payments` — платежи (заготовка под TipTop Pay, интеграция — отдельный этап)
- `delivery` — зоны доставки (упрощённая MVP-версия: районы города с фиксированной ценой)
- `content` — редактируемые блоки главной страницы (Hero, Instagram, промо)
- `reviews` — отзывы клиентов с модерацией
- `main` — общие вьюхи сайта (главная страница)

## Запуск локально

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Заполните `.env` реальными значениями (минимум — параметры подключения к PostgreSQL).
Поднимите PostgreSQL и создайте базу `blackpepper` (имя берётся из `.env`).

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py setup_roles
python manage.py runserver
```

`setup_roles` создаёт группу «Менеджер магазина» с доступом только к товарам,
категориям, заказам, блокам главной страницы и отзывам. Владелец (superuser)
видит всё, включая доставку, платежи и системные разделы.
