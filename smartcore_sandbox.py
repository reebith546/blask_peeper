#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Консольная песочница для платёжного шлюза SmartCore / TipTop Pay.

Отдельный самостоятельный скрипт — НЕ часть Django-проекта. Нужен, чтобы
вручную прогнать интеграцию в тестовой среде до того, как встраивать её в сайт:

  1) создать платёж (initPayment) и получить ссылку на форму оплаты;
  2) открыть форму и оплатить тестовой картой;
  3) проверить статус платежа (check);
  4) проверить алгоритм подписи callback'а;
  5) поднять локальный сервер и принять настоящий callback от шлюза.

Зависимостей нет — только стандартная библиотека Python 3.8+.
Запуск:  python smartcore_sandbox.py
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================================
#  НАСТРОЙКИ — ЗАПОЛНИ ЭТИ ПОЛЯ значениями из личного кабинета SmartCore/TipTop.
#  Всё, что помечено  # <-- ВВЕСТИ  — обязательно.
# ============================================================================

# [1] Хост API. В документации SmartCore — api-gateway.smartcore.pro.
#     У TipTop Pay KZ может быть свой домен — уточни в кабинете и подставь сюда.
API_BASE = "https://api-gateway.smartcore.pro"                  # <-- ВВЕСТИ (проверить хост)

# [2] Имя мерчант-аккаунта. Для теста — с суффиксом "-sandbox",
#     например "KZT-sandbox". Точное значение — в кабинете.
ACCOUNT = "PUT-SANDBOX-ACCOUNT-HERE"                            # <-- ВВЕСТИ

# [3] Логин для HTTP Basic-авторизации (merchantKey).
MERCHANT_KEY = "PUT-MERCHANT-KEY-HERE"                          # <-- ВВЕСТИ

# [4] Пароль для Basic-авторизации И одновременно ключ подписи callback'ов (secret).
SECRET = "PUT-SECRET-HERE"                                      # <-- ВВЕСТИ

# [5] Валюта в формате Alpha-3. Подтверди в кабинете, что аккаунт проводит KZT.
CURRENCY = "KZT"                                               # <-- ВВЕСТИ (подтвердить KZT)

# [6] Публичный URL, куда шлюз пришлёт callback (POST) о результате платежа.
#     Для локального теста подними туннель (cloudflared / ngrok) на порт [9]
#     и вставь сюда его адрес + "/callback", напр.:
#       https://random-name.trycloudflare.com/callback
#     Можно оставить пустым, если callback пока не тестируешь (пункты 1–4 меню
#     работают и без него).
CALLBACK_URL = ""                                              # <-- ВВЕСТИ (для теста callback)

# [7]/[8] Куда шлюз вернёт клиента из формы оплаты. Для песочницы годится любой
#     рабочий URL — эти страницы в тесте не важны, статус берём из callback/check.
SUCCESS_URL = "https://example.com/pay/success"                # <-- можно оставить
FAIL_URL = "https://example.com/pay/fail"                      # <-- можно оставить

# [9] Порт локального сервера приёма callback'а (меню, пункт 5).
CALLBACK_SERVER_PORT = 8888

# --- Тестовые данные платежа (можно менять) ---------------------------------
TEST_AMOUNT_MAJOR = 10.00        # сумма в тенге (основные единицы, не тиыны)
TEST_CUSTOMER = {
    "customer_first_name": "Test",
    "customer_last_name":  "Buyer",
    "customer_email":      "test.buyer@example.com",
    "customer_phone":      "+77000000000",
    "customer_address":    "ул. Желтоксан, 87а",
    "customer_city":       "Almaty",
    "customer_zip_code":   "050000",
    "customer_country":    "KZ",       # если initPayment ругается — попробуй "KAZ"
    "customer_ip_address": "127.0.0.1",
}

# Тестовые карты из документации SmartCore:
#   4012 0000 0000 3010  — успех (frictionless 3DS)
#   4003 8301 7187 4018  — 3DS-challenge (успех или отказ по выбору)
#   любая другая         — отказ
#   срок — любой будущий, CVV — любые 3 цифры.

# Куда сохраняем order_id последнего созданного платежа (чтобы переиспользовать в "check").
_LAST_ORDER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smartcore_last_order")

# Если проверка НАСТОЯЩИХ callback'ов не проходит — переключи в False:
#   True  = параметр "sign" исключается из строки подписи (стандартное поведение);
#   False = в строку подписи попадают все пришедшие параметры.
EXCLUDE_SIGN_FROM_SIGNATURE = True


# ============================================================================
#  Низкоуровневые помощники
# ============================================================================

def _check_config():
    missing = []
    if "PUT-" in ACCOUNT:
        missing.append("ACCOUNT [2]")
    if "PUT-" in MERCHANT_KEY:
        missing.append("MERCHANT_KEY [3]")
    if "PUT-" in SECRET:
        missing.append("SECRET [4]")
    if missing:
        print("\n  [!]  Не заполнены обязательные настройки: " + ", ".join(missing))
        print("     Открой smartcore_sandbox.py и впиши значения из кабинета.\n")
        return False
    return True


def _auth_header():
    raw = f"{MERCHANT_KEY}:{SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _post_json(path, payload):
    """POST JSON на API_BASE + path с Basic-авторизацией. Возвращает (http_status, dict)."""
    url = API_BASE.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", _auth_header())

    print(f"\n>> POST {url}")
    safe = dict(payload)
    print("  body: " + json.dumps(safe, ensure_ascii=False))

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        print(f"  [FAIL] сеть/URL: {e}")
        return None, None

    print(f"<< HTTP {status}")
    print("  " + text)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, {"_raw": text}


def compute_sign(params, secret=None):
    """Подпись по алгоритму SmartCore: значения параметров, отсортированные ПО КЛЮЧУ,
    склеенные через '|', HMAC-SHA256 (hex). Параметр 'sign' в подпись не входит."""
    secret = secret or SECRET
    keys = sorted(k for k in params.keys()
                  if not (EXCLUDE_SIGN_FROM_SIGNATURE and k == "sign"))
    base_str = "|".join(str(params[k]) for k in keys)
    digest = hmac.new(secret.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return base_str, digest


def _save_last_order(order_id):
    try:
        with open(_LAST_ORDER_FILE, "w", encoding="utf-8") as f:
            f.write(order_id)
    except OSError:
        pass


def _load_last_order():
    try:
        with open(_LAST_ORDER_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# ============================================================================
#  Операции
# ============================================================================

def action_init_payment():
    if not _check_config():
        return

    order_id = f"sbx-{int(time.time())}"
    payload = {
        "account": ACCOUNT,
        "currency": CURRENCY,
        "order_id": order_id,
        # Сумма: основными единицами (тенге). Если API попросит "amount" —
        # замени строку ниже на:   "amount": int(round(TEST_AMOUNT_MAJOR * 100)),
        "amount_major": TEST_AMOUNT_MAJOR,
        "purpose": "Тестовый заказ (песочница)",
        **TEST_CUSTOMER,
    }
    if SUCCESS_URL:
        payload["success_url"] = SUCCESS_URL
    if FAIL_URL:
        payload["fail_url"] = FAIL_URL
    if CALLBACK_URL:
        payload["callback_url"] = CALLBACK_URL
    else:
        print("  (i) CALLBACK_URL [6] не задан — уведомление о результате не придёт, "
              "статус смотри через пункт 3 меню.")

    status, data = _post_json("/initPayment", payload)
    if not data:
        return

    form_url = data.get("form_url")
    gw_order_id = data.get("order_id")
    err = data.get("err") or data.get("error") or data.get("errorMessage")

    if form_url:
        _save_last_order(order_id)
        print("\n  Платёж создан.")
        print(f"    наш order_id ......... {order_id}   (сохранён для пункта 3)")
        print(f"    order_id шлюза ....... {gw_order_id}")
        print(f"    ССЫЛКА НА ОПЛАТУ ..... {form_url}")
        if _ask_yes("    Открыть форму оплаты в браузере?"):
            webbrowser.open(form_url)
    else:
        print("\n  [FAIL] form_url не получен.")
        if err:
            print(f"    причина: {err}")
        print("    Проверь ACCOUNT/MERCHANT_KEY/SECRET/валюту и набор customer_*-полей.")


def action_open_form():
    print("  Вставь form_url из ответа initPayment (или Enter, чтобы отменить):")
    url = input("  form_url> ").strip()
    if url:
        webbrowser.open(url)


def action_check_status():
    if not _check_config():
        return
    default = _load_last_order()
    prompt = f"  order_id для проверки [{default}]> " if default else "  order_id для проверки> "
    order_id = input(prompt).strip() or default
    if not order_id:
        print("  order_id не задан.")
        return

    status, data = _post_json("/check", {"order_id": order_id})
    if not data:
        return

    code = data.get("status")
    meaning = {
        0: "ожидает перехода на форму",
        1: "в обработке",
        2: "УСПЕХ — оплачено",
        -1: "ОТКАЗ",
    }.get(code, "неизвестно")
    print(f"\n  Статус: {code} ({meaning})")
    if data.get("finalAmount") is not None:
        print(f"  Сумма (final): {data.get('finalAmount')}  из {data.get('amount')}")
    if data.get("errorMessage"):
        print(f"  Сообщение: {data.get('errorMessage')}")
    if data.get("card"):
        print(f"  Карта: {data.get('card')}")


def action_test_signature():
    """Оффлайн-проверка: генерируем подпись как шлюз, затем убеждаемся, что
    наша verify её принимает. Плюс можно вставить настоящий callback и проверить его."""
    if "PUT-" in SECRET:
        print("  Сначала впиши SECRET [4].")
        return

    sample = {
        "orderId": "sbx-1700000000",
        "status": "2",
        "amount": "1000",
        "currency": CURRENCY,
        "type": "Payment",
    }
    base_str, sign = compute_sign(sample)
    sample_with_sign = dict(sample, sign=sign)

    print("\n  Синтетический callback:")
    print("    параметры: " + urllib.parse.urlencode(sample))
    print("    строка подписи: " + base_str)
    print("    sign (HMAC-SHA256 hex): " + sign)

    _, check_sign = compute_sign(sample_with_sign)
    print("  Проверка round-trip: " + ("OK" if hmac.compare_digest(check_sign, sign) else "НЕ СОШЛОСЬ [FAIL]"))

    print("\n  Хочешь проверить НАСТОЯЩИЙ callback? Вставь его тело одной строкой")
    print("  (формат k=v&k=v&...&sign=...), либо Enter чтобы пропустить:")
    raw = input("  body> ").strip()
    if not raw:
        return
    params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    got = params.get("sign", "")
    _, expected = compute_sign(params)
    print(f"    пришло:   {got}")
    print(f"    ожидаем:  {expected}")
    ok = bool(got) and hmac.compare_digest(got, expected)
    print("    ПОДПИСЬ ВЕРНА" if ok else "    ПОДПИСЬ НЕ СОВПАЛА [FAIL]  "
          "(попробуй переключить EXCLUDE_SIGN_FROM_SIGNATURE)")


def action_callback_server():
    if "PUT-" in SECRET:
        print("  Сначала впиши SECRET [4].")
        return

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code=200, text="OK"):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))

        def do_GET(self):
            self._reply(200, "smartcore_sandbox callback listener is up")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            ctype = (self.headers.get("Content-Type") or "").lower()

            if "application/json" in ctype:
                try:
                    params = json.loads(raw)
                except json.JSONDecodeError:
                    params = {}
            else:
                params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

            print("\n" + "=" * 70)
            print(f"CALLBACK  {self.path}  ({ctype or 'no content-type'})")
            print("raw: " + raw)
            for k in sorted(params):
                print(f"  {k} = {params[k]}")

            got = str(params.get("sign", ""))
            _, expected = compute_sign(params)
            ok = bool(got) and hmac.compare_digest(got, expected)
            print(f"  подпись: {'ВЕРНА' if ok else 'НЕ СОВПАЛА [FAIL]'}")
            if not ok:
                print(f"    пришло:  {got}")
                print(f"    ожидаем: {expected}")

            st = str(params.get("status", ""))
            print("  вывод: " + {"2": "оплачено", "-1": "отказ"}.get(st, f"status={st}"))
            print("=" * 70)

            # Шлюзу достаточно ответа 200 OK.
            self._reply(200, "OK")

        def log_message(self, *args):
            pass  # свой вывод выше

    addr = ("0.0.0.0", CALLBACK_SERVER_PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"\n  Слушаю callback на  http://localhost:{CALLBACK_SERVER_PORT}/")
    print(f"  Подними туннель на этот порт и укажи его URL + '/callback' в CALLBACK_URL [6].")
    print("  Ctrl+C — остановить.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Остановлено.")
    finally:
        httpd.server_close()


# ============================================================================
#  Меню
# ============================================================================

def _ask_yes(q):
    return input(f"{q} [y/N] ").strip().lower() in ("y", "yes", "д", "да")


MENU = """
========= SmartCore / TipTop Pay — песочница =========
  1) Создать платёж (initPayment) >> получить ссылку на оплату
  2) Открыть форму оплаты в браузере (вставить form_url)
  3) Проверить статус платежа (check)
  4) Проверить алгоритм подписи callback'а
  5) Запустить локальный сервер приёма callback'а
  0) Выход
"""

ACTIONS = {
    "1": action_init_payment,
    "2": action_open_form,
    "3": action_check_status,
    "4": action_test_signature,
    "5": action_callback_server,
}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.version_info < (3, 8):
        print("Нужен Python 3.8+")
        return
    while True:
        print(MENU)
        choice = input("Выбор> ").strip()
        if choice in ("0", "q", "exit", ""):
            return
        action = ACTIONS.get(choice)
        if not action:
            print("  Нет такого пункта.")
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\n  Прервано.")
        except Exception as e:  # песочница — показываем ошибку, не падаем
            print(f"  [FAIL] Ошибка: {e!r}")


if __name__ == "__main__":
    main()
