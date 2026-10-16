# راهنمای توسعه

این سند برای هر دو توسعه‌دهنده (و دستیار کدنویسی هوش مصنوعی هرکدام) نوشته شده است: چگونه
محیط را بالا بیاوریم، تست بگیریم و قابلیت جدید اضافه کنیم. مرجع ساختار کد:
[`architecture.md`](architecture.md).

---

## ۱. راه‌اندازی محیط

```bash
git clone https://github.com/mohammadreza4188/iran-obd-tuner
cd iran-obd-tuner

python -m venv .venv
source .venv/Scripts/activate     # Windows (git-bash)  |  Linux/macOS: source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"   # کد + pytest + ruff
```

بررسی صحت نصب:

```bash
python -m pytest -q          # باید همه تست‌ها پاس شوند
python -m iran_obd_tuner diagnose
```

**بدون نصب هم می‌توانید کار کنید:** `pyproject.toml` مسیر `src` را به pytest معرفی کرده است،
پس `python -m pytest` کار می‌کند و اجرای برنامه با `PYTHONPATH=src python -m iran_obd_tuner`
ممکن است.

### extras و اینکه چه زمانی لازم می‌شوند

| extra | محتوا | چه زمانی |
|---|---|---|
| `dev` | pytest, ruff | همیشه (برای همه اعضا) |
| `obd` | python-obd, pyserial | کار روی آداپتور واقعی ELM327 (فاز ۱) |
| `can` | python-can | کار روی CAN (فاز ۲+) |
| `bench` | udsoncan, crcmod | کار روی Bench/UDS و چک‌سام (فاز ۴+) |
| `gui` | PySide6, pyqtgraph | کار روی رابط گرافیکی (فاز ۱) |
| `full` | همه موارد بالا | اجرای کامل نرم‌افزار دسکتاپ |

هیچ‌کدام از این وابستگی‌ها برای اجرای خروجی فعلی (تشخیص شبیه‌سازی‌شده) لازم نیستند.

## ۲. تنظیمات و متغیرهای محیطی

```bash
cp config/example.toml config/local.toml
```

| متغیر محیطی | معادل در فایل | پیش‌فرض |
|---|---|---|
| `IRAN_OBD_TUNER_ADAPTER` | `adapter` | `mock` |
| `IRAN_OBD_TUNER_SERIAL_PORT` | `serial_port` | خالی |
| `IRAN_OBD_TUNER_SERIAL_BAUDRATE` | `serial_baudrate` | `38400` |
| `IRAN_OBD_TUNER_CONNECT_TIMEOUT_S` | `connect_timeout_s` | `5.0` |
| `IRAN_OBD_TUNER_COMMAND_TIMEOUT_S` | `command_timeout_s` | `2.0` |
| `IRAN_OBD_TUNER_LIVE_DATA_PIDS` | `live_data_pids` | `010C,010D,0105,0111,010F,0142` |
| `IRAN_OBD_TUNER_LOG_LEVEL` | `log_level` | `INFO` |
| `IRAN_OBD_TUNER_LOG_FILE` | `log_file` | خالی (فقط stderr) |
| `IRAN_OBD_TUNER_OUTPUT_DIR` | `output_dir` | `output` |
| `IRAN_OBD_TUNER_KNOWLEDGE_DIR` | `knowledge_dir` | خالی |

ترتیب اولویت: پیش‌فرض کد ← فایل TOML ← متغیر محیطی. کلید ناشناخته خطا می‌دهد.
`config/local.toml` و `.env` در `.gitignore` هستند.

## ۳. اجرا و بررسی سریع

```bash
python scripts/check.py                    # compile + ruff check + ruff format --check + pytest
python -m pytest -q                        # فقط تست‌ها
python -m pytest tests/unit -q             # فقط یونیت‌تست‌ها
python -m pytest -q -k diagnostic          # انتخاب بر اساس نام
python -m ruff check . && python -m ruff format .
python -m iran_obd_tuner diagnose --json | python -m json.tool
python -m iran_obd_tuner config            # تنظیمات نهایی و منبع آن‌ها
```

`scripts/check.py` همان سه گیت CI را روی هر پلتفرمی اجرا می‌کند و اگر pytest/ruff نصب نبود،
آن مرحله را «skip» گزارش می‌دهد (به‌جای شکست).

## ۴. افزودن تست

- تست‌های واحد در `tests/unit/`، تست‌های انتها-به-انتها (CLI) در `tests/integration/`.
- داده‌های شبیه‌سازی‌شده در `tests/fixtures/` (نمونه: `simulated_vehicle.json`).
- fixtureهای مشترک در `tests/conftest.py`.
- **هیچ تستی نباید به خودرو، آداپتور، شبکه یا سخت‌افزار نیاز داشته باشد.** همه‌چیز از طریق
  `MockObdAdapter` و `MockScenario` (با `FailureMode` برای شبیه‌سازی timeout/خطای فریم) تست شود.
- نام فایل‌ها یکتا باشد (`tests/unit/test_x.py` و `tests/integration/test_y.py`) تا با
  حالت پیش‌فرض import در pytest تداخل نکند.

## ۵. افزودن یک قابلیت: مسیرهای متعارف

### الف) PID جدید

1. ورودی در `PID_CATALOG` در `src/iran_obd_tuner/core/pids.py` (شناسه در فرم درخواست مثل `010C`).
2. در صورت نیاز به لیست پیش‌فرض، `DEFAULT_LIVE_DATA_PIDS` و `config/example.toml` را به‌روز کنید.
3. یک تست در `tests/unit/test_pid_catalog.py` (و در صورت لزوم سناریوی شبیه‌ساز).

### ب) آداپتور واقعی

1. ماژول جدید `src/iran_obd_tuner/hardware/<name>.py` با پیاده‌سازی `ObdAdapter`.
2. import کتابخانه خارجی **داخل** همان ماژول (نه در `hardware/__init__.py`) و مدیریت نبود
   وابستگی با پیام واضح.
3. ثبت در `hardware/registry.py`: `implemented=True`، پروتکل‌های واقعی مورد ادعا، پیش‌نیاز سخت‌افزاری.
4. تست با پاسخ‌های ساختگی (fake transport)، بدون سخت‌افزار؛ به‌علاوه یک تست برای مسیر «پاسخ
   نامعتبر/تایم‌اوت/PID پشتیبانی‌نشده».
5. به‌روزرسانی جدول‌های قابلیت در `README.md` و `docs/architecture.md`.

### ج) داده دانش

- برای داده محلی: پوشه‌ای مثل `knowledge/local/` با فایل `dtc_catalog.json` و
  `knowledge_dir = "knowledge/local"` در `config/local.toml` (پوشه در `.gitignore` است).
- برای داده منتشرشده: افزودن به `src/iran_obd_tuner/database/data/dtc_catalog.json` **با ذکر منبع**
  در `metadata.source`. داده بدون شاهد (حدس) اضافه نکنید.

### د) قابلیت نوشتنی (فلش/کالیبراسیون)

در این مرحله **مجاز نیست**. اگر تیم تصمیم گرفت، باید هم‌زمان با پیاده‌سازی:
تأیید صریح کارفرما، شواهد فنی خانواده ECU، عبور همه مسیرها از
`tuning.interfaces.assert_write_allowed()`، مسیر پشتیبان‌گیری/بازگردانی و تست روی ECU Bench
(دور از خودرو) انجام شود. اسکلت فعلی فقط مرز و پیش‌نیازها را تعریف کرده است.

## ۶. CI

`.github/workflows/ci.yml` روی هر push به `main` و هر PR اجرا می‌شود: پایتون ۳٫۱۱/۳٫۱۲/۳٫۱۳،
`s pip install -e ".[dev]"`، سپس `ruff check`، `ruff format --check` و `pytest`.
extras سخت‌افزاری/گرافیکی در CI نصب **نمی‌شوند** (چون بدون سخت‌افزار واقعی قابل اعتبارسنجی
نیستند). اگر تغییری فقط با `[obd]`/`[gui]` قابل تست است، در توضیح PR بنویسید که دستی بررسی شده.

## ۷. عیب‌یابی

| نشانه | علت محتمل | راه‌حل |
|---|---|---|
| `ModuleNotFoundError: iran_obd_tuner` | بسته نصب نشده و `src` در مسیر نیست | `pip install -e ".[dev]"` یا `PYTHONPATH=src` |
| `configuration file not found: config/local.toml` | فایل محلی نساخته‌اید ولی `--config` داده‌اید | `cp config/example.toml config/local.toml` |
| `unknown key(s) in ...` | خطای تایپی در کلید TOML | مقایسه با `config/example.toml` |
| `unknown adapter '...'` | نام آداپتور اشتباه | `iran-obd-tuner adapters` |
| `adapter 'elm327' is declared but not implemented` | فاز ۱ پیاده نشده | استفاده از `mock` |
| نصب `python-obd` شکست می‌خورد | بسته قدیمی (۰٫۷٫۱) و پایتون جدید | تست روی پایتون ۳٫۱۱/۳٫۱۲؛ در صورت لزوم با تیم هماهنگ شود |
| نصب PySide6 روی Windows شکست می‌خورد | نبود wheel مناسب نسخه پایتون | اجرای `pip install "PySide6>=6.6.0"` با نسخه پایتون پیشنهادی خود بسته |
| نبود شبکه/PyPI | محدودیت شبکه | اجرای تست‌ها بدون extras ممکن است؛ `pytest` از `src` استفاده می‌کند |
| diffهای خالی با تغییر خط‌پایان | CRLF/LF | `.gitattributes` و `.editorconfig` را رعایت کنید (`git add --renormalize .`) |
| `db.sqlite3`/`output/` ناخواسته | خروجی اجرای محلی | این مسیرها در `.gitignore` هستند؛ حذفشان کنید |

## ۸. استانداردهای کد

- **زبان:** شناسه‌ها، docstringها و کامنت‌های کد: انگلیسی. مستندات (`README`, `docs/`, `CONTRIBUTING`): فارسی.
- type annotations اجباری؛ `from __future__ import annotations` در ابتدای همه ماژول‌ها.
- `dataclass(frozen=True, slots=True)` برای مدل‌ها؛ اعتبارسنجی در `__post_init__`.
- طول خط ۱۰۰، قالب‌بندی و لینت با **ruff** (تنها ابزار پروژه).
- خطاها از درخت `core.exceptions` استفاده کنند؛ `print` در کد کتابخانه‌ای ممنوع (فقط `logging`).
- هر ماژول یک docstring داشته باشد که بگوید **چه چیزی پیاده نشده** (قاعده صداقت پروژه).
