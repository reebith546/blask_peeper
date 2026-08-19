# Деплой Black Pepper Flower Bar на VPS

Пошаговый чек-лист от чистого сервера до рабочего HTTPS-сайта. Рассчитан на
**Ubuntu 24.04 LTS** (в ней уже есть Python 3.12 из коробки — на 22.04 его
пришлось бы ставить отдельно через PPA).

Всё, что в `<угловых скобках>`, — замените на свои значения перед запуском
команды.

## 0. Что понадобится заранее

- IP-адрес сервера (после покупки Cloud-сервера у Hoster.kz)
- Домен(ы), которые будете использовать — например `blackpepperflowerbar.kz`
- SSH-доступ root (Hoster.kz присылает пароль/ключ при выдаче сервера)

## 1. Первичная настройка сервера

Подключитесь по SSH под root и создайте отдельного пользователя — работать
и деплоить проект под root не стоит:

```bash
adduser deploy
usermod -aG sudo deploy
```

Дальше все команды — уже под этим пользователем (`su - deploy`).

Обновите систему и поставьте базовые пакеты:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip git nginx ufw curl
```

Docker (нужен для PostgreSQL — используем тот же `docker-compose.yml`, что и
в разработке, ничего заново придумывать не надо):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

После этой команды перелогиньтесь (`exit` и зайдите по SSH заново), чтобы
права группы `docker` подхватились.

Базовый firewall — открываем только SSH, HTTP, HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 2. DNS

В панели управления доменом (там, где регистрировали `blackpepperflowerbar.kz`)
пропишите A-записи на IP нового сервера:

```
blackpepperflowerbar.kz.      A     <IP сервера>
www.blackpepperflowerbar.kz.  A     <IP сервера>
```

Изменения DNS расходятся не мгновенно (от нескольких минут до нескольких
часов) — можно продолжать настройку сервера, не дожидаясь.

## 3. Код проекта

```bash
sudo mkdir -p /srv/blackpepper
sudo chown $USER:www-data /srv/blackpepper
git clone https://github.com/reebith546/blask_peeper.git /srv/blackpepper
cd /srv/blackpepper
git checkout main
```

## 4. Python-окружение

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. `.env` с реальными значениями

```bash
cp .env.example .env
nano .env
```

Заполните:

- `SECRET_KEY` — сгенерируйте новый, **не берите dev-значение**:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
- `DEBUG=False`
- `ALLOWED_HOSTS=blackpepperflowerbar.kz,www.blackpepperflowerbar.kz`
- `CSRF_TRUSTED_ORIGINS=https://blackpepperflowerbar.kz,https://www.blackpepperflowerbar.kz`
- `DB_PASSWORD` — смените с дефолтного `postgres` на что-то надёжное
- `TIPTOP_PUBLIC_ID` / `TIPTOP_API_SECRET` — когда определитесь с платёжкой
- `YANDEX_SUGGEST_API_KEY` / `YANDEX_GEOCODER_API_KEY` — те же, что уже
  использовали локально

## 6. База данных

```bash
docker compose up -d
```

Подождите несколько секунд, пока Postgres поднимется, затем:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python manage.py setup_roles
```

## 7. Gunicorn (процесс приложения)

Отредактируйте `deploy/gunicorn.service` — замените `deploy_user` на
реальное имя пользователя (`deploy`, если следовали этому чек-листу
дословно), затем:

```bash
sudo cp deploy/gunicorn.service /etc/systemd/system/blackpepper.service
sudo systemctl daemon-reload
sudo systemctl enable --now blackpepper
sudo systemctl status blackpepper
```

В выводе `status` должно быть `active (running)`. Если нет — смотрите логи:

```bash
sudo journalctl -u blackpepper -n 50
```

## 8. nginx

Отредактируйте `deploy/nginx.conf` — проверьте домены в `server_name`,
затем:

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/blackpepper
sudo ln -s /etc/nginx/sites-available/blackpepper /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

На этом этапе сайт уже должен открываться по `http://blackpepperflowerbar.kz`
(если DNS успел разойтись).

## 9. HTTPS (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d blackpepperflowerbar.kz -d www.blackpepperflowerbar.kz
```

Certbot сам допишет конфиг nginx под HTTPS и настроит автопродление
сертификата (раз в 90 дней, без вашего участия).

## 10. Проверка

- `https://blackpepperflowerbar.kz/` — открывается главная, есть замок в адресной строке
- `https://blackpepperflowerbar.kz/admin/` — вход в админку под superuser
- Пройти полный флоу: каталог → корзина → чекаут → заказ создан
- `python manage.py test_yandex_maps` — ключи Яндекса работают с боевого сервера

## Обновление проекта после первого деплоя

Дальнейшие изменения выкатываются так:

```bash
cd /srv/blackpepper
git pull
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart blackpepper
```

## Резервные копии

Не забывайте про `pg_dump` и копию папки `media/` — см. инструкцию в истории
чата или попросите заново, если понадобится. На проде это особенно важно —
там уже настоящие заказы клиентов.
