#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Консольная песочница для TipTop Pay (ex-CloudPayments Kazakhstan).

Отдельный самостоятельный скрипт — НЕ часть Django-проекта. Нужен, чтобы
вручную прогнать интеграцию до встраивания её в сайт:

  1) создать счёт на оплату (/orders/create) и получить ссылку на форму;
  2) открыть форму и оплатить тестовой картой;
  3) проверить статус платежа (/payments/find);
  4) проверить алгоритм подписи webhook'а (Content-HMAC);
  5) поднять локальный сервер и принять настоящий webhook от шлюза.

Зависимостей нет — только стандартная библиотека Python 3.8+.
Запуск:  python tiptoppay_sandbox.py

Документация: https://developers.tiptoppay.kz  (API-хост https://api.tiptoppay.kz)
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
#  НАСТРОЙКИ — ЗАПОЛНИ ЭТИ ПОЛЯ значениями из личного кабинета TipTop Pay.
#  Всё, что помечено  # <-- ВВЕСТИ  — обязательно.
# ============================================================================

# [1] Хост API. Для боевого и тестового аккаунта одинаковый.
API_BASE = "https://api.tiptoppay.kz"                          # <-- обычно менять не нужно

# [2] Public ID из кабинета, вида "pk_xxxxxxxxxxxxxxxxxxxxxxxxx".
PUBLIC_ID = "PUT-PUBLIC-ID-HERE"                               # <-- ВВЕСТИ

# [3] API Secret из кабинета (он же пароль API и ключ подписи webhook).
API_SECRET = "PUT-API-SECRET-HERE"                             # <-- ВВЕСТИ

# [4] Валюта (Alpha-3). Убедись, что аккаунт проводит KZT.
CURRENCY = "KZT"                                               # <-- ВВЕСТИ (подтвердить KZT)

# [5]/[6] Куда шлюз вернёт клиента из формы оплаты. Для песочницы годится любой
#     рабочий URL — эти страницы в тесте не важны, статус берём из webhook/find.
SUCCESS_URL = "https://example.com/pay/success"                # <-- можно оставить
FAIL_URL = "https://example.com/pay/fail"                      # <-- можно оставить

# [7] Порт локального сервера приёма webhook'а (меню, пункт 5).
#     Адрес webhook (домен + путь) задаётся в ЛК TipTop Pay, не в запросе.
#     Для локального теста подними туннель (cloudflared / ngrok) на этот порт
#     и укажи в кабинете напр.  https://random-name.trycloudflare.com/callback
CALLBACK_SERVER_PORT = 8888

# --- Тестовые данные платежа (можно менять) ---------------------------------
TEST_AMOUNT = 10.00        # сумма в тенге
TEST_EMAIL = "test.buyer@example.com"
TEST_PHONE = "+77000000000"

# Тестовые карты TipTop Pay / CloudPayments:
#   успех без 3DS ......... 4111 1111 1111 1111
#   успех с 3DS (пароль) .. см. кабинет (обычно любой код / 12345678)
#   отказ ................. любая другая
#   срок — любой будущий, CVV — любые 3 цифры.

# Куда сохраняем InvoiceId последнего счёта (чтобы переиспользовать в "find").
_LAST_INVOICE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".tiptoppay_last_invoice")


# ============================================================================
#  Низкоуровневые помощники
# ============================================================================

def _check_config():
    missing = []
    if "PUT-" in PUBLIC_ID:
        missing.append("PUBLIC_ID [2]")
    if "PUT-" in API_SECRET:
        missing.append("API_SECRET [3]")
    if missing:
        print("\n  [!]  Не заполнены обязательные настройки: " + ", ".join(missing))
        print("     Открой tiptoppay_sandbox.py и впиши значения из кабинета.\n")
        return False
    return True


def _auth_header():
    raw = f"{PUBLIC_ID}:{API_SECRET}".encode("utf-8")
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
    print("  body: " + json.dumps(payload, ensure_ascii=False))

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


def content_hmac(raw_body, secret=None):
    """Заголовок Content-HMAC: Base64(HMAC_SHA256(тело_запроса, API Secret))."""
    secret = secret or API_SECRET
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _save_last_invoice(invoice_id):
    try:
        with open(_LAST_INVOICE_FILE, "w", encoding="utf-8") as f:
            f.write(invoice_id)
    except OSError:
        pass


def _load_last_invoice():
    try:
        with open(_LAST_INVOICE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# ============================================================================
#  Операции
# ============================================================================

def action_create_order():
    if not _check_config():
        return

    invoice_id = f"sbx-{int(time.time())}"
    payload = {
        "Amount": TEST_AMOUNT,
        "Currency": CURRENCY,
        "Description": "Тестовый заказ (песочница)",
        "InvoiceId": invoice_id,
        "AccountId": TEST_EMAIL,
        "Email": TEST_EMAIL,
        "Phone": TEST_PHONE,
        "SuccessRedirectUrl": SUCCESS_URL,
        "FailRedirectUrl": FAIL_URL,
        "JsonData": {"source": "sandbox"},
    }

    status, data = _post_json("/orders/create", payload)
    if not data:
        return

    model = data.get("Model") or {}
    form_url = model.get("Url")
    if data.get("Success") and form_url:
        _save_last_invoice(invoice_id)
        print("\n  Счёт создан.")
        print(f"    наш InvoiceId ........ {invoice_id}   (сохранён для пункта 3)")
        print(f"    Id счёта в шлюзе ..... {model.get('Id')}")
        print(f"    номер счёта .......... {model.get('Number')}")
        print(f"    ССЫЛКА НА ОПЛАТУ ..... {form_url}")
        if _ask_yes("    Открыть форму оплаты в браузере?"):
            webbrowser.open(form_url)
    else:
        print("\n  [FAIL] ссылка на оплату не получена.")
        print(f"    Success: {data.get('Success')}   Message: {data.get('Message')}")
        print("    Проверь PUBLIC_ID / API_SECRET / валюту аккаунта.")


def action_open_form():
    print("  Вставь ссылку на форму (Model.Url) или Enter, чтобы отменить:")
    url = input("  url> ").strip()
    if url:
        webbrowser.open(url)


def action_find_status():
    if not _check_config():
        return
    default = _load_last_invoice()
    prompt = f"  InvoiceId для проверки [{default}]> " if default else "  InvoiceId для проверки> "
    invoice_id = input(prompt).strip() or default
    if not invoice_id:
        print("  InvoiceId не задан.")
        return

    status, data = _post_json("/payments/find", {"InvoiceId": invoice_id})
    if not data:
        return

    model = data.get("Model") or {}
    st = model.get("Status")
    meaning = {
        "AwaitingAuthentication": "ждёт 3DS",
        "Authorized": "захолдировано (двухстадийная)",
        "Completed": "УСПЕХ — оплачено",
        "Cancelled": "отменено",
        "Declined": "ОТКАЗ",
    }.get(st, "нет платежа / неизвестно")
    print(f"\n  Статус: {st} ({meaning})")
    if model.get("Amount") is not None:
        print(f"  Сумма: {model.get('Amount')} {model.get('Currency', '')}")
    if model.get("CardLastFour"):
        print(f"  Карта: **** {model.get('CardLastFour')}  {model.get('CardType', '')}")
    if model.get("Reason"):
        print(f"  Причина отказа: {model.get('Reason')} ({model.get('ReasonCode')})")


def action_test_signature():
    """Генерируем Content-HMAC как шлюз и проверяем, что наша сверка его принимает.
    Плюс можно вставить настоящий webhook (тело + заголовок) и проверить его."""
    if "PUT-" in API_SECRET:
        print("  Сначала впиши API_SECRET [3].")
        return

    sample_body = urllib.parse.urlencode({
        "TransactionId": "1700000000",
        "InvoiceId": "sbx-1700000000",
        "Amount": "10.00",
        "Currency": CURRENCY,
        "Status": "Completed",
        "TestMode": "1",
    })
    sig = content_hmac(sample_body)
    print("\n  Синтетический webhook:")
    print("    тело:          " + sample_body)
    print("    Content-HMAC:  " + sig)
    ok = hmac.compare_digest(content_hmac(sample_body), sig)
    print("  Проверка round-trip: " + ("OK" if ok else "НЕ СОШЛОСЬ [FAIL]"))

    print("\n  Проверить НАСТОЯЩИЙ webhook? Вставь СЫРОЕ тело одной строкой")
    print("  (k=v&k=v&...), либо Enter чтобы пропустить:")
    raw = input("  body> ").strip()
    if not raw:
        return
    got = input("  Content-HMAC из заголовка> ").strip()
    expected = content_hmac(raw)
    print(f"    пришло:   {got}")
    print(f"    ожидаем:  {expected}")
    good = bool(got) and hmac.compare_digest(got, expected)
    print("    ПОДПИСЬ ВЕРНА" if good else "    ПОДПИСЬ НЕ СОВПАЛА [FAIL]")


def action_callback_server():
    if "PUT-" in API_SECRET:
        print("  Сначала впиши API_SECRET [3].")
        return

    class Handler(BaseHTTPRequestHandler):
        def _reply_json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply_json({"status": "tiptoppay_sandbox listener is up"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            got = (self.headers.get("Content-HMAC")
                   or self.headers.get("X-Content-HMAC") or "")
            params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

            print("\n" + "=" * 70)
            print(f"WEBHOOK  {self.path}")
            print("raw: " + raw)
            for k in sorted(params):
                print(f"  {k} = {params[k]}")

            expected = content_hmac(raw)
            ok = bool(got) and hmac.compare_digest(got, expected)
            print(f"  Content-HMAC: {'ВЕРНА' if ok else 'НЕ СОВПАЛА [FAIL]'}")
            if not ok:
                print(f"    пришло:  {got}")
                print(f"    ожидаем: {expected}")

            st = str(params.get("Status", ""))
            qs = urllib.parse.urlparse(self.path).query
            ntype = dict(urllib.parse.parse_qsl(qs)).get("type", "")
            print(f"  тип (?type=): {ntype or '—'}   Status: {st or '—'}")
            print("=" * 70)

            # Шлюз ждёт JSON {"code": 0} = уведомление принято.
            self._reply_json({"code": 0})

        def log_message(self, *args):
            pass

    addr = ("0.0.0.0", CALLBACK_SERVER_PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"\n  Слушаю webhook на  http://localhost:{CALLBACK_SERVER_PORT}/")
    print("  Подними туннель на этот порт и укажи его URL в ЛК TipTop Pay")
    print("  (по одному адресу на событие, напр. .../callback?type=pay).")
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
========= TipTop Pay — песочница =========
  1) Создать счёт (/orders/create) >> получить ссылку на оплату
  2) Открыть форму оплаты в браузере (вставить ссылку)
  3) Проверить статус платежа (/payments/find)
  4) Проверить алгоритм подписи webhook'а (Content-HMAC)
  5) Запустить локальный сервер приёма webhook'а
  0) Выход
"""

ACTIONS = {
    "1": action_create_order,
    "2": action_open_form,
    "3": action_find_status,
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
