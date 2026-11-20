# iran-obd-tuner

نرم‌افزار یکپارچه **تشخیص و تیونینگ خودرو** با پایتون، مخصوص بازار ایران.

> **English summary.** A Python toolkit for OBD-II diagnostics and (in later phases) bench
> ECU calibration for the Iranian market. Today it ships a working, hardware-independent,
> strictly **read-only** diagnostic workflow driven by an in-memory simulated adapter, a
> PySide6 dashboard (phase 1) that uses the very same adapter contract, and the project scaffolding
> (packaging, config, logging, tests, CI, docs) the team needs to grow it. No ECU write/flash
> functionality exists — clearing trouble codes from the dashboard is refused by the safety
> policy — and none is planned before explicit approval and vehicle-specific validation.

---

## هدف پروژه

ساخت یک ابزار یکپارچه که بتواند هم‌زمان:

- عیب‌یابی و خواندن کدهای خطا (DTC) و مشاهده Live Data از طریق OBD-II
- تیونینگ و ریمپ ECU (فعلاً فقط در حالت Bench و در فازهای بعدی)

را پوشش دهد. سند مفهومی کامل پروژه: [`docs/idea-report.md`](docs/idea-report.md).

## اصول طراحی

1. **جداسازی کامل Diagnostics و Tuning**: هر عملکردی که وضعیت خودرو را تغییر دهد، خارج از
   مسیر پیش‌فرض و پشت یک مرز ایمنی صریح قرار دارد.
2. **اولویت‌بندی خودروها**: تمرکز اولیه روی خانواده‌های پرتیراژ (ایران‌خودرو، سایپا و یک برند چینی).
3. **دیتابیس دانش به‌عنوان دارایی اصلی**: تعاریف ECU، نقشه‌ها، چک‌سام و توضیح کدهای خطا.
4. **توسعه بدون خودرو**: کل مسیر تشخیصی با آداپتور شبیه‌ساز و بدون سخت‌افزار قابل اجرا و تست است.

## وضعیت فعلی (فاز ۰ + ادغام داشبورد فاز ۱)

### پیاده‌سازی‌شده (کد واقعی، قابل اجرا)

| قابلیت | محل |
|---|---|
| ورودی خط فرمان با دستورات `version`, `adapters`, `config`, `diagnose` | `src/iran_obd_tuner/app/cli.py` |
| بارگذاری تنظیمات: پیش‌فرض ← فایل TOML ← متغیرهای محیطی | `src/iran_obd_tuner/core/config.py` |
| راه‌اندازی لاگ استاندارد (stderr و فایل اختیاری) | `src/iran_obd_tuner/core/logging.py` |
| مدل‌های داده (Measurement/Unit/VehicleInfo) و کاتالوگ PID | `core/models.py`, `core/pids.py` |
| ساختار کد خطا (SAE J2012) و مرز ایمنی | `core/dtc.py`, `core/safety.py` |
| جریان کامل تشخیص (شناسایی، Live Data، DTC، گزارش JSON) | `src/iran_obd_tuner/diagnostics/service.py` |
| نشست داده زنده داشبورد روی همان قرارداد `ObdAdapter`: وضعیت جداگانه هر PID (پشتیبانی‌نشده / بدون پاسخ / پاسخ نامعتبر)، بدون هیچ مقدار جایگزین | `src/iran_obd_tuner/diagnostics/live_data.py` |
| آداپتور شبیه‌ساز (ایستا + چرخه رانندگی زمان‌مند و قطعی) + تزریق خطای ارتباطی | `src/iran_obd_tuner/hardware/mock.py` |
| فهرست درگاه‌های سریال بدون باز کردن هیچ درگاهی (pyserial اختیاری) | `src/iran_obd_tuner/hardware/serial_ports.py` |
| **آداپتور ELM327 فقط‌خواندنی** (سریال/USB): راه‌اندازی، تشخیص پروتکل، PIDهای پشتیبانی‌شده، Live Data، کدهای خطا (سرویس ۰۳)، VIN (سرویس ۰۹)، فهرست سفید دستورها — **روی سخت‌افزار واقعی اعتبارسنجی نشده** | `src/iran_obd_tuner/hardware/elm327.py`, `elm327_protocol.py`, `transport.py` |
| فرمول‌های decode پارامترها طبق SAE J1979 (شامل اصلاح سوخت کوتاه‌مدت/بلندمدت `0106`/`0107`) | `src/iran_obd_tuner/core/pids.py` |
| وضعیت چراغ چک (MIL)، تعداد کدهای تأییدشده و آمادگی مانیتورها (PID `0101`) — جدول بیت‌ها با J1979 اصلی **تأیید نشده** | `src/iran_obd_tuner/core/monitors.py` |
| کدهای خطای ذخیره‌شده / در انتظار / دائمی (سرویس ۰۳ / ۰۷ / ۰A)؛ هر دسته وضعیت خودش را دارد و نبود یک دسته بقیه را خراب نمی‌کند | `src/iran_obd_tuner/diagnostics/service.py` |
| Freeze Frame شماره ۰۰ (سرویس ۰۲): کد عامل و ۸ پارامتر؛ پارامتر ذخیره‌نشده با «—» | `src/iran_obd_tuner/diagnostics/service.py`, `ui/diagnostics_view.py` |
| نمایشگر ترافیک خام آداپتور (TX/RX) در زیرتب «رویدادها»، با توقف نمایش، پاک کردن و کپی؛ بافر محدود و ایمن برای چند thread. شبیه‌ساز فقط درخواست و مقدار decodeشده را با برچسب «شبیه‌سازی» ثبت می‌کند، نه فریم هگز ساختگی | `src/iran_obd_tuner/core/traffic.py`, `ui/traffic_view.py` |
| تب «عملکرد»: زمان‌سنج ۰ تا ۱۰۰ (با زمان ۰ تا ۶۰، مسافت و **عدم‌قطعیت اعلام‌شده**) و دینوی مجازی (توان و گشتاور **تخمینی** از یک کشش با دریچه گاز کامل). هر دو کاملاً غیرفعال‌اند و فقط داده poll موجود را می‌خوانند؛ «poll سریع» هنگام اندازه‌گیری فقط دور، سرعت و دریچه گاز را می‌خواند (دستور کمتر، نه بیشتر) | `src/iran_obd_tuner/diagnostics/performance.py`, `diagnostics/dyno.py`, `ui/performance_view.py` |
| گزارش JSON نسخه ۲ (`schema_version: 2`) با `monitor_status`، `dtc_categories` و `freeze_frame` | `src/iran_obd_tuner/diagnostics/models.py` |
| خواندن داده در thread پس‌زمینه (رابط کاربری هنگام جستجوی پروتکل قفل نمی‌شود) | `src/iran_obd_tuner/ui/worker.py` |
| دیتابیس دانش JSON (زیرمجموعه عمومی کدهای خطا) | `src/iran_obd_tuner/database/` |
| سیم‌کشی داشبورد در لایه app (تنظیمات، لاگ، رجیستری آداپتور) | `src/iran_obd_tuner/app/dashboard.py` + `app/gui_main.py` |
| داشبورد گرافیکی PySide6: گیج‌ها، نمودار زنده، کدهای خطا، شناسایی خودرو، لاگ، دو زبان (فارسی/انگلیسی) | `src/iran_obd_tuner/ui/` |
| دکمه بازخوانی درگاه‌های سریال در نوار ابزار | `src/iran_obd_tuner/ui/main_window.py` |
| ضبط Live Data در CSV: زمان UTC، واحد هر ستون و ستون `simulated` | `src/iran_obd_tuner/diagnostics/recorder.py` |
| نوار وضعیت (Status Bar) با پیام‌های لحظه‌ای و شمارنده نمونه‌ها | `src/iran_obd_tuner/ui/main_window.py` |
| ترجمه کامل رابط کاربری و پیام‌های لاگ (فارسی/انگلیسی) با کلیدهای i18n | `src/iran_obd_tuner/ui/i18n.py` |
| آیکون برنامه (تولید برنامه‌نویسی‌شده با QPainter، بدون فایل خارجی) | `src/iran_obd_tuner/ui/icon.py` |
| مجموعه تست خودکار بدون سخت‌افزار و بدون نمایشگر، شامل تست مرز لایه‌ها و کامل بودن ترجمه | `tests/` |

### شبیه‌سازی‌شده (هیچ داده واقعی خودرویی در آن نیست)

- **تمام** مقادیر Live Data و کدهای خطای خروجی، شبیه‌سازی‌شده‌اند (`adapter = "mock"` برای CLI و
  «حالت شبیه‌سازی» برای داشبورد).
- گزارش‌ها فیلد `simulated: true` را حمل می‌کنند و در خروجی متنی صریحاً `SIMULATED` چاپ می‌شود.
- در داشبورد، تا وقتی نشست شبیه‌سازی باز است یک نوار هشدار دائمی دیده می‌شود. فایل‌های CSV
  ستون `simulated` دارند و نام پیشنهادی‌شان به `_simulated.csv` ختم می‌شود. VIN شبیه‌سازی
  (`SIMULATEDVIN00001`) با برچسب «داده شبیه‌سازی‌شده» نمایش داده می‌شود.
- توضیحات کدهای خطا از یک زیرمجموعه دستی‌نویس غیرمرجع (generic) می‌آید که متادیتای آن
  `authoritative: false` است.

### برنامه‌ریزی‌شده (پیاده‌سازی نشده — بدون ادعای پشتیبانی)

| قابلیت | فاز | پیش‌نیاز |
|---|---|---|
| اعتبارسنجی آداپتور ELM327 روی سخت‌افزار و خودروی واقعی (پژو پارس TU5) | ۱ | آداپتور فیزیکی + خودرو |
| تفکیک چند ECU با headerها، freeze frameهای غیر از ۰۰، PID `0141` | ۱ | — |
| تأیید جدول بیت‌های PID `0101` با متن SAE J1979 | ۱ | دسترسی به استاندارد |
| مقایسه زمان‌سنج و دینوی مجازی با GPS و دینامومتر واقعی | ۱ | سخت‌افزار و خودرو |
| CAN و UDS (SocketCAN/PCAN/Kvaser) | ۲ | `[can]`، `[bench]` + سخت‌افزار |
| مطالعه و تکمیل دیتابیس دانش خودروهای ایرانی | ۳ | پژوهش مستند، نه حدس |
| خواندن/ویرایش ROM در حالت Bench و تصحیح چک‌سام | ۴ | تأیید صریح + اعتبارسنجی خودرویی |
| فلش Bench و مکانیزم بازگردانی | ۵ | کارفرمای تأییدشده و مسیر کنترل‌شده |

**هیچ‌کدام از موارد «برنامه‌ریزی‌شده» در این نسخه کد ندارند.** آداپتورهای اعلام‌شده در
`hardware/registry.py` با `implemented=False` ثبت شده‌اند و درخواست آن‌ها با خطای صریح
`AdapterNotImplementedError` رد می‌شود:

```
$ iran-obd-tuner adapters
declared adapters: 3 (1 implemented)
```
## ضبط داده در CSV

داشبورد می‌تواند داده‌های Live را در فایل CSV ذخیره کند تا بعداً در Excel/پایتون تحلیل شوند:

1. در تب **تنظیمات**، حالت **شبیه‌سازی** یا **آداپتور واقعی** را انتخاب کنید (برای آداپتور واقعی، درگاه سریال را در نوار ابزار انتخاب کنید).
2. دکمه **اتصال** را بزنید.
3. دکمه **⏺ (ضبط)** در نوار ابزار فعال می‌شود. با زدن آن پنجره ذخیره باز می‌شود و مسیر
   پیشنهادی در پوشه `output_dir` تنظیمات است (پیش‌فرض: `output/`).
4. هرچه نمونه‌ها ذخیره می‌شوند، در **نوار وضعیت** شمارنده به‌روز می‌شود (`⏺ ۱۵۰ نمونه`).
5. برای توقف، روی **⏹** بزنید. با قطع اتصال یا بستن پنجره هم ضبط متوقف و فایل بسته می‌شود.

ساختار فایل: ستون `timestamp_utc` (ISO 8601 با میلی‌ثانیه و `+00:00`)، ستون `simulated`
(`true`/`false`) و برای هر پارامتر یک ستون با نام `<PID> <نام> [<واحد>]`، مثل
`010C Engine speed [rpm]`. اگر پارامتری خوانده نشود، خانه آن **خالی** می‌ماند و هیچ عدد
جایگزینی نوشته نمی‌شود. پوشه `output/` در `.gitignore` است و commit نمی‌شود.

## مرز ایمنی (مهم)

- رفتار پیش‌فرض **فقط خواندن** است (`OperationClass.READ_ONLY`).
- پاک کردن کدهای خطا (سرویس ۰۴)، تست عملگرها، فلش و نوشتن کالیبراسیون **پیاده‌سازی نشده‌اند**.
  فراخوانی آن‌ها با `SafetyGuardError` رد می‌شود؛ حتی اگر سیاست ایمنی به‌صورت کدی اجازه بدهد،
  با `UnsupportedOperationError` مشخص می‌شود که پیاده‌سازی وجود ندارد.
- در داشبورد، دکمه «پاک کردن کدها» در چیدمان باقی مانده، اما **کار نمی‌کند**. درخواست به گارد
  ایمنی آداپتور (`ObdAdapter.clear_diagnostic_trouble_codes`) می‌رود و رد می‌شود. کاربر یک
  پنجره توضیح می‌بیند و رویداد در لاگ ثبت می‌شود. **هیچ پیاده‌سازی‌ای برای پاک کردن کدها
  (سرویس ۰۴) در کد وجود ندارد.** مسیر قبلی python-OBD در Milestone 1 حذف شد و
  `tests/unit/test_architecture.py` از بازگشت آن جلوگیری می‌کند.
- سیاست ایمنی **از فایل تنظیمات خوانده نمی‌شود**؛ فقط در کد قابل ساخت است تا یک اشتباه در
  `config/local.toml` نتواند نوشتن روی ECU را فعال کند.
- هنگام setup، تست و CI هیچ فرمانی به خودرو ارسال نمی‌شود و هیچ تستی پورت سریال باز نمی‌کند.
  فهرست درگاه‌ها با یک ماژول pyserial جعلی و آداپتور ELM327 با یک transport اسکریپتی تست می‌شود.
- آداپتور ELM327 یک **فهرست سفید دستور** دارد: فقط دستورهای AT پیکربندی و سرویس‌های OBD `01`، `03` و `09`
  ارسال می‌شوند. سرویس ۰۴، ۰۸، دستورهای UDS و `AT SP` (که EEPROM آداپتور را می‌نویسد) پیش از ارسال رد
  می‌شوند. برای جستجوی پروتکل از `AT TP 0` استفاده می‌شود که چیزی در آداپتور ذخیره نمی‌کند.
- درگاه سریال **هرگز خودکار جستجو نمی‌شود**؛ کاربر باید آن را صریحاً انتخاب کند تا فرمانی به دستگاه ناشناخته
  ارسال نشود. اگر دستگاه روی درگاه به `ATZ` به‌عنوان ELM327 پاسخ ندهد، هیچ فرمان دیگری ارسال نمی‌شود.
- انتخاب «آداپتور واقعی» در داشبورد **هرگز** بی‌صدا به شبیه‌ساز تبدیل نمی‌شود؛ اگر اتصال شکست بخورد،
  خطا نمایش داده می‌شود.
- پیش‌نیازهای هر نوشتن آینده در `tuning/interfaces.py` (`BenchSafetyChecklist`) فهرست شده‌اند.

## پیش‌نیازها

- **پایتون ۳٫۱۱ یا بالاتر** (توسعه و تست روی ۳٫۱۲ و ۳٫۱۳ انجام شده است).
- برای تشخیص شبیه‌سازی‌شده (CLI و داشبورد): **هیچ سخت‌افزاری لازم نیست**.
- برای داشبورد گرافیکی: `[gui]` (PySide6). بدون آن، CLI کامل کار می‌کند و تست‌های رابط
  گرافیکی خودکار skip می‌شوند.
- برای آداپتور واقعی: آداپتور ELM327 سازگار (USB/سریال)، `[obd]` (pyserial) و انتخاب صریح درگاه.
  این مسیر هنوز روی سخت‌افزار واقعی **اعتبارسنجی نشده** است.
- برای Bench (فاز ۴+، هنوز پیاده نشده): رابط CAN (SocketCAN/PCAN/Kvaser) و منبع تغذیه پایدار ECU.

## نصب

```bash
python -m venv .venv
# Windows (bash/git-bash):  source .venv/Scripts/activate
# Linux/macOS:              source .venv/bin/activate

# فقط کد و تست‌ها (بدون هیچ وابستگی خارجی):
python -m pip install -e ".[dev]"

# داشبورد گرافیکی:
python -m pip install -e ".[gui]"

# همه‌چیز (شامل آداپتور/سخت‌افزار برای فازهای بعدی):
python -m pip install -e ".[full]"
```

جایگزین بدون نصب: تست‌ها با `python -m pytest` هم اجرا می‌شوند (مسیر `src` از
`pyproject.toml` خوانده می‌شود) و اجرای برنامه با `PYTHONPATH=src python -m iran_obd_tuner` ممکن است.

## پیکربندی

```bash
cp config/example.toml config/local.toml   # این فایل در .gitignore است
```

ترتیب اولویت: **پیش‌فرض‌های کد ← `config/local.toml` ← متغیرهای محیطی `IRAN_OBD_TUNER_*`**.
کلید ناشناخته یا مقدار نامعتبر با خطای پیکربندی رد می‌شود (بی‌صدا نادیده گرفته نمی‌شود).
نمونه:

```bash
IRAN_OBD_TUNER_LOG_LEVEL=DEBUG iran-obd-tuner config
```

هیچ رمز یا کلید محرمانه‌ای لازم نیست و نباید در مخزن ذخیره شود.

## اجرا

```bash
# رابط خط فرمان (فقط‌خواندنی، بدون نیاز به GUI)
iran-obd-tuner version      # نسخه و نسخه پایتون
iran-obd-tuner adapters     # آداپتورهای اعلام‌شده و وضعیت پیاده‌سازی
iran-obd-tuner config       # تنظیمات نهایی و منبع آن‌ها
iran-obd-tuner diagnose     # یک جلسه تشخیص فقط‌خواندنی با آداپتور شبیه‌ساز
iran-obd-tuner diagnose --json --output output/report.json
iran-obd-tuner diagnose --pids 010C,010D,0105 --adapter mock
iran-obd-tuner diagnose --adapter elm327 --port COM3   # آداپتور واقعی، فقط‌خواندنی

# داشبورد گرافیکی (نیازمند extras به نام gui)
iran-obd-tuner-gui
# معادل: python -m iran_obd_tuner.app.gui_main
```

همین دستورات CLI با `python -m iran_obd_tuner <command>` هم کار می‌کنند.
کدهای خروجی: `0` موفق، `1` خطای اجرا، `2` خطای کاربرد/پیکربندی، `130` قطع با Ctrl+C.

در داشبورد: حالت «شبیه‌سازی» را انتخاب کنید و «اتصال» را بزنید تا بدون آداپتور، گیج‌ها و
نمودار زنده کار کنند. پارامتری که خوانده نشود با «—» و دلیلش (مثلاً «پشتیبانی نمی‌شود»)
نمایش داده می‌شود، نه با صفر. داشبورد همان تنظیمات CLI را می‌خواند (`config/local.toml` و
متغیرهای `IRAN_OBD_TUNER_*`). برای ضبط داده در CSV، دکمه ⏺ در نوار ابزار را بزنید (جزئیات
در بخش «ضبط داده در CSV»). روی ماشین بدون نمایشگر (CI/سرور) می‌توانید اجرا را با
`QT_QPA_PLATFORM=offscreen` بالا بیاورید.

## تست و کیفیت کد

```bash
python -m pytest -q              # مجموعه تست، بدون سخت‌افزار و بدون نمایشگر
python -m ruff check .           # لینت
python -m ruff format --check .  # بررسی قالب‌بندی
python scripts/check.py          # هر سه گیت بالا با هم (همان چیزی که CI اجرا می‌کند)
```

`scripts/check.py` سخت‌گیر است: اگر ruff یا pytest نصب نباشد، آن گیت **شکست** حساب می‌شود،
نه skip. فقط با `--allow-missing` ابزار غایب به‌صورت «skipped» گزارش می‌شود.

تست‌های رابط گرافیکی (`tests/integration/test_gui_window.py`) روی پلتفرم `offscreen` اجرا
می‌شوند و از سیم‌کشی واقعی برنامه (`AppDashboardBackend`) استفاده می‌کنند. اگر `[gui]` نصب
نباشد، خودکار skip می‌شوند. برای اجرای آن‌ها روی ماشین خودتان `pip install -e ".[dev,gui]"`
لازم است.

CI (`.github/workflows/ci.yml`) روی پایتون ۳٫۱۱، ۳٫۱۲ و ۳٫۱۳ همین سه گیت را اجرا می‌کند.
extras سخت‌افزاری/گرافیکی عمداً در CI نصب نمی‌شوند (نیاز به سخت‌افزار واقعی و تست دستی دارند).

## ساختار پروژه

```
iran-obd-tuner/
├─ src/iran_obd_tuner/               کد برنامه (بسته واقعی، src-layout)
│  ├─ app/                           ورودی‌ها و سیم‌کشی: cli.py، gui_main.py، application.py، dashboard.py
│  ├─ core/                          تنظیمات، لاگ، مدل‌ها، کاتالوگ PID/DTC، مرز ایمنی
│  ├─ hardware/                      قرارداد آداپتور (base.py)، شبیه‌ساز (mock.py)، رجیستری، فهرست درگاه‌ها
│  ├─ diagnostics/                   گزارش یک‌باره (service.py)، نشست داده زنده (live_data.py)، ضبط CSV (recorder.py)
│  ├─ database/                      دیتابیس دانش (JSON) + داده‌های همراه بسته
│  ├─ tuning/                        رابط‌های Bench و پیش‌نیازهای نوشتن (بدون پیاده‌سازی)
│  └─ ui/                            داشبورد PySide6: backend.py، gauges.py، i18n.py، icon.py، safety_modal.py، main_window.py
├─ tests/                            unit/ ، integration/ (CLI + GUI)، fixtures/
├─ config/example.toml               نمونه پیکربندی (کپی به config/local.toml)
├─ docs/                             idea-report.md، architecture.md، development.md
├─ scripts/check.py                  اجرای گیت‌های کیفیت به‌صورت یکجا
├─ .github/                          CI و قالب Pull Request
├─ pyproject.toml                    متادیتا، extras، نقاط ورود، تنظیمات pytest/ruff
├─ requirements.txt                  نصب کامل وابستگی‌های اجرایی (هم‌ارز extras)
├─ CONTRIBUTING.md                   گردش‌کار شاخه/کامیت/PR
└─ README.md
```

> **تاریخچه ساختار:** ابتدا پوشه‌های خالی `src/<module>/` به بسته `src/iran_obd_tuner/`
> منتقل شدند. سپس کدهای فاز ۱ همکار (که در ساختار قدیمی `src/ui/`, `src/diagnostics/`,
> `src/main.py` نوشته شده بودند) با `git mv` به همان بسته منتقل شدند:
> `src/ui/gauges.py` → `ui/gauges.py`، `src/ui/i18n.py` → `ui/i18n.py`،
> `src/ui/main_window.py` → `ui/main_window.py`، `src/diagnostics/obd_service.py` →
> `diagnostics/obd_service.py`، `src/main.py` → `app/gui_main.py`. در آن مرحله هیچ کدی حذف نشد؛
> تاریخچه فایل‌ها در گیت حفظ شده است (git rename). در Milestone 1، `diagnostics/obd_service.py`
> (همراه مسیر python-OBD پاک کردن کدها) و `tuning/ecu_id.py` با تأیید صاحب پروژه حذف شدند و
> داشبورد به `diagnostics/live_data.py` منتقل شد. جزئیات در `docs/architecture.md` (ADR ۱۲).

## مستندات

- [`docs/architecture.md`](docs/architecture.md) — لایه‌ها، قواعد وابستگی، مرز ایمنی، نقاط توسعه
- [`docs/development.md`](docs/development.md) — راه‌اندازی محیط، تست، افزودن آداپتور/PID، عیب‌یابی
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — شاخه، کامیت، Pull Request، بازبینی
- [`docs/idea-report.md`](docs/idea-report.md) — سند مفهومی و نقشه راه محصول
- [`docs/ai-assistant-rules.md`](docs/ai-assistant-rules.md) — قواعد پروژه برای دستیارهای هوش مصنوعی

## نقشه راه

- **فاز ۰ (انجام‌شده):** ساختار پروژه، مرز ایمنی، جریان تشخیص شبیه‌سازی‌شده، تست و CI
- **فاز ۱ (نیمه‌انجام‌شده):** داشبورد PySide6 با داده شبیه‌سازی‌شده کار می‌کند. Milestone 1
  انجام شده است: داشبورد و CLI حالا یک مسیر داده واحد (`ObdAdapter`) دارند، داده ساختگی و
  مسیر پاک کردن کدها حذف شده و داده شبیه‌سازی همه‌جا برچسب دارد. Milestone 2 هم انجام شده است:
  آداپتور ELM327 فقط‌خواندنی و خواندن در پس‌زمینه. اعتبارسنجی روی آداپتور و خودروی واقعی باقی مانده است.
- **فاز ۲:** CAN و UDS، شناسایی VIN و خانواده ECU
- **فاز ۳:** دیتابیس دانش خودروهای ایرانی بر پایه شواهد مستند
- **فاز ۴:** خواندن و ویرایش ROM در Bench + تصحیح چک‌سام (نیازمند تأیید)
- **فاز ۵:** فلش Bench با پشتیبان‌گیری اجباری و بازگردانی (نیازمند تأیید)
- **فازهای بعدی:** فلش از طریق OBD (فقط خانواده‌های کاملاً تست‌شده) و پشتیبانی هیبریدی

## مشارکت‌کنندگان

- محمد رضا
- عباس

## مجوز

MIT — فایل [`LICENSE`](LICENSE).

> **هشدار:** تغییر کالیبراسیون ECU می‌تواند به موتور، جعبه‌فرمان، سیستم اگزوز و ایمنی خودرو
> آسیب بزند و ممکن است گارانتی و الزامات قانونی را نقض کند. این پروژه فعلاً هیچ قابلیت
> نوشتنی ندارد؛ هر قابلیت نوشتن در آینده نیازمند اعتبارسنجی مخصوص خودرو و مسیر تست
> کنترل‌شده است.
