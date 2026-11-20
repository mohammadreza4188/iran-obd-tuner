# معماری پروژه iran-obd-tuner

این سند مرجع ساختار کد است: مسئولیت هر پوشه، قواعد وابستگی، تصمیم‌های معماری و نقاط توسعه.
سند مفهومی محصول جداگانه است: [`idea-report.md`](idea-report.md).

---

## ۱. اصول معماری

1. **تفکیک لایه‌ها با وابستگی یک‌طرفه.** هر لایه فقط به لایه‌های پایین‌تر خود وابسته است.
2. **هسته بدون وابستگی خارجی.** `core` فقط کتابخانه استاندارد را import می‌کند؛ نتیجه: کل
   جریان تشخیص بدون خودرو، بدون آداپتور، بدون GUI و بدون شبکه قابل اجرا و تست است.
3. **سخت‌افزار پشت قرارداد.** هیچ لایه‌ای مستقیم با سریال/CAN کار نمی‌کند؛ همه‌چیز از
   `hardware.base.ObdAdapter` عبور می‌کند.
4. **صداقت دربارهٔ پشتیبانی.** آنچه پیاده نشده، صریحاً «پیاده‌نشده» اعلام می‌شود (رجیستری،
   گزارش‌ها، مستندات) — نه شبیه‌سازی پنهان و نه ادعای پشتیبانی.
5. **ایمنی، پیش‌فرض است.** هر عملیاتی که وضعیت خودرو را تغییر دهد، رد می‌شود مگر با سیاست
   ایمنی صریح در کد (بخش ۵).

---

## ۲. لایه‌ها و قواعد وابستگی

```
app/           ورودی‌ها و سیم‌کشی (CLI، GUI، Application)     ← همه لایه‌ها را می‌شناسد
  ↑
diagnostics/   جریان تشخیص + سرویس داشبورد + مدل گزارش      ← core, hardware, database
  ↑
hardware/      قرارداد آداپتور + شبیه‌ساز + رجیستری          ← core
database/      دیتابیس دانش (JSON) + داده‌های همراه          ← core
tuning/        رابط‌های Bench و مرز ایمنی نوشتن              ← core
ui/            داشبورد PySide6 (برگ، هیچ لایه‌ای آن را import نمی‌کند)  ← core, diagnostics
  ↑
core/          تنظیمات، لاگ، مدل‌ها، کاتالوگ PID/DTC، ایمنی    ← فقط کتابخانه استاندارد
```

قواعد اجرایی:

- `core` هیچ‌گاه از لایه‌های بالا import نمی‌کند.
- `hardware` از `diagnostics` و `database` import نمی‌کند (لایه انتقال، تفسیر نمی‌کند).
- `database` از `hardware`/`diagnostics` import نمی‌کند.
- `tuning` جایگزین `diagnostics` نیست: مسیرهای جدا، فراخوانی‌های جدا.
- import کتابخانه‌های خارجی (`pyserial`, `PySide6`, `python-can`, `udsoncan`) فقط داخل
  ماژول همان آداپتور/رابط انجام می‌شود، نه در `__init__` بسته‌ها — تا نبودِ آن وابستگی
  «قابل گزارش» بماند و برنامه بدون آن هم اجرا شود.
- `ui` هیچ‌وقت آداپتور انتخاب نمی‌کند و `hardware`/`database`/`tuning` را import نمی‌کند. پنجره
  یک `ui.backend.DashboardBackend` (Protocol) دریافت می‌کند که `app/dashboard.py` آن را
  پیاده می‌کند.
- این قواعد فقط روی کاغذ نیستند: `tests/unit/test_architecture.py` importهای همه ماژول‌ها را با
  AST بررسی می‌کند. چند چیز دیگر را هم ممنوع می‌کند: import کتابخانه خارجی در سطح ماژول (به جز
  PySide6 در `ui` و ورودی GUI)، import تنبل (داخل تابع) بیرون از `hardware`، هر ارجاع به
  دستور `CLEAR_DTC`، و (Milestone 4) import `can`/`isotp`/`udsoncan` بیرون از توابع `hardware`،
  literal درخواست UDS با SID نوشتنی، فراخوانی `can.Bus` بدون `interface=` و ساخت سرور ECU
  شبیه‌سازی بیرون از `hardware/uds_link.py`.

جدول کامل کاتالوگ مسیرها:

| مسیر | مسئولیت | وضعیت |
|---|---|---|
| `core/config.py` | بارگذاری تنظیمات: پیش‌فرض ← TOML ← محیط | پیاده‌شده |
| `core/logging.py` | پیکربندی logging استاندارد، بدون تغییر وضعیت جهانی در import | پیاده‌شده |
| `core/models.py` | `Measurement`, `Unit`, `VehicleInfo`, `utc_now` | پیاده‌شده |
| `core/pids.py` | کاتالوگ PID سرویس ۰۱ + اعتبارسنجی شناسه‌ها | پیاده‌شده (زیرمجموعه) |
| `core/dtc.py` | ساختار کد خطا (سیستم/مرجع بودن) طبق SAE J2012 | پیاده‌شده |
| `core/safety.py` | `OperationClass`, `SafetyPolicy`, `require()` | پیاده‌شده |
| `core/exceptions.py` | درخت خطاها با ریشه `IranObdTunerError` | پیاده‌شده |
| `hardware/base.py` | `ObdAdapter` (چرخه اتصال، خواندن‌ها، رد عملیات نوشتنی)، `AdapterInfo.simulated` | پیاده‌شده |
| `hardware/mock.py` | `MockObdAdapter`, `MockScenario` (ایستا + `drive_cycle`)، `FailureMode`، `MotionProfile` | پیاده‌شده |
| `hardware/registry.py` | اعلام آداپتورها + ساخت آداپتور | پیاده‌شده |
| `hardware/serial_ports.py` | فهرست درگاه‌های سریال بدون باز کردن آن‌ها (pyserial تنبل) | پیاده‌شده |
| `hardware/transport.py` | قرارداد `Transport` + `PySerialTransport` (pyserial تنبل) | پیاده‌شده |
| `hardware/elm327_protocol.py` | تجزیه خالص پاسخ‌های ELM327 + فهرست سفید دستورها | پیاده‌شده (تست با نمونه‌های datasheet) |
| `hardware/elm327.py` | `Elm327Adapter` فقط‌خواندنی | پیاده‌شده، **روی سخت‌افزار اعتبارسنجی نشده** |
| `hardware/can_bus.py` | `CanFrame`، قرارداد `CanBus`، `PythonCanBus` (python-can تنبل، نام رابط همیشه صریح) | پیاده‌شده، **روی رابط واقعی اعتبارسنجی نشده** |
| `hardware/isotp.py` | ISO-TP خودِ پروژه: `IsoTpChannel` (SF/FF/CF/FC، BS، STmin، Wait)، `functional_request` | پیاده‌شده |
| `hardware/uds.py` | `UdsClient` فقط‌خواندنی، فهرست سفید `check_request_allowed`، NRCها، decode DTC، `discover_ecus` | پیاده‌شده |
| `hardware/uds_sim.py` | `SimulatedUdsEcu` قطعی + `VirtualBusEcuServer` (فقط باس `virtual`) | پیاده‌شده (شبیه‌سازی) |
| `hardware/uds_link.py` | `UdsLink`/`UdsLinkConfig`: رابط CAN ← ISO-TP ← `UdsClient`؛ `virtual-sim` = باس مجازی + ECU شبیه‌سازی | پیاده‌شده |
| `diagnostics/ecu_identification.py` | `read_ecu_identification` (DID و DTC مستقل، fallback جلسه Extended با opt-in)، `EcuIdentification`، `UdsIdentificationSession` | پیاده‌شده |
| `ui/uds_view.py` | بخش «شناسایی ECU با UDS» در تب شناسایی | پیاده‌شده |
| `ui/worker.py` | `BackgroundRunner`: یک thread پس‌زمینه، اجرای ترتیبی کارهای آداپتور | پیاده‌شده |
| `core/monitors.py` | decode PID `0101`: MIL، تعداد DTC، نوع احتراق، آمادگی مانیتورها | پیاده‌شده (جدول بیت‌ها با J1979 اصلی تأیید نشده) |
| `core/dtc.py` → `DtcStatus` | دسته کد خطا: stored (۰۳) / pending (۰۷) / permanent (۰A) | پیاده‌شده |
| `ui/diagnostics_view.py` | تب کدهای خطا: دسته‌ها، فیلتر، جدول Freeze Frame | پیاده‌شده |
| `core/traffic.py` | `TrafficLog`: بافر حلقوی محدود و thread-safe برای خطوط TX/RX/INFO | پیاده‌شده |
| `ui/traffic_view.py` | زیرتب «ترافیک خام»: توقف نمایش، پاک کردن، کپی | پیاده‌شده |
| `diagnostics/performance.py` | `AccelerationTimer`: ماشین حالت ۰ تا ۱۰۰، درون‌یابی، عدم‌قطعیت | پیاده‌شده (تخمینی) |
| `diagnostics/dyno.py` | دینوی مجازی: تفکیک دنده، برازش `v = k·rpm`، رگرسیون درجه دو محلی، فیزیک | پیاده‌شده (تخمینی؛ فقط روش عددی تأیید شده) |
| `ui/performance_view.py`, `ui/chart.py` | تب «عملکرد» و نمودار دومحوره QPainter | پیاده‌شده |
| `diagnostics/service.py` | `DiagnosticService.collect_report()` + تفسیر مشترک کد خطا و شناسه خودرو | پیاده‌شده |
| `diagnostics/live_data.py` | `LiveDataSession`, `LiveSnapshot`, `PidReading`, `PidStatus` | پیاده‌شده |
| `diagnostics/recorder.py` | `CsvRecorder` (UTC، واحد، ستون `simulated`) | پیاده‌شده |
| `diagnostics/models.py` | `TroubleCode`, `DiagnosticReport` | پیاده‌شده |
| `database/repository.py` | `KnowledgeRepository` + پیاده‌سازی JSON | پیاده‌شده |
| `database/data/dtc_catalog.json` | زیرمجموعه دستی کدهای خطای عمومی | پیاده‌شده (غیرمرجع) |
| `tuning/interfaces.py` | `CalibrationReader/Writer`, `BenchSafetyChecklist` | فقط قرارداد |
| `app/cli.py`, `app/application.py` | CLI و سیم‌کشی | پیاده‌شده |
| `app/dashboard.py` | `AppDashboardBackend`: انتخاب آداپتور برای حالت شبیه‌سازی/واقعی | پیاده‌شده |
| `app/gui_main.py` | ورودی داشبورد (`iran-obd-tuner-gui`): تنظیمات + لاگ + backend | پیاده‌شده (نیازمند `[gui]`) |
| `ui/backend.py` | قرارداد `DashboardBackend` (بدون Qt) | پیاده‌شده |
| `ui/gauges.py`, `ui/i18n.py`, `ui/main_window.py`, `ui/safety_modal.py`, `ui/icon.py` | ویجت گیج، رشته‌های فارسی/انگلیسی، پنجره اصلی، پنجره رد ایمنی، آیکون | پیاده‌شده (نیازمند `[gui]`؛ `i18n` بدون Qt) |

---

## ۳. جریان یک جلسه تشخیص

```
CLI/UI
  └─ app.Application.from_settings()      ← تنظیمات + لاگ + آداپتور + دیتابیس دانش
       └─ diagnostics.DiagnosticService.collect_report(pids)
            ├─ adapter.session(timeout_s)   ← باز/بسته شدن تضمینی اتصال
            ├─ adapter.read_vin()
            ├─ adapter.read_supported_pids()
            ├─ adapter.read_measurements(pids)   → MeasurementBatch(+ skipped_pids)
            ├─ read_monitor_status_result(adapter)   → MonitorStatusResult (PID 0101)
            ├─ read_dtc_categories(adapter, knowledge)  → ۳ × DtcCategoryResult (۰۳ / ۰۷ / ۰A)
            ├─ read_freeze_frame_snapshot(adapter, ...)  → FreezeFrameSnapshot (۰۲، frame ۰۰)
            └─ knowledge.describe_dtc(code)  → TroubleCode (+ توضیح و status)
                 → DiagnosticReport (schema_version = 2, read_only = True)

هر بخش توسعه‌یافته (۰۱۰۱، هر دسته DTC، freeze frame) **مستقل** خوانده می‌شود و `ReadOutcome`
خودش را دارد (`ok` / `unsupported` / `no_response` / `error`). پشتیبانی‌نشده یا بی‌پاسخ بودن یک
بخش، بقیه گزارش را خراب نمی‌کند و هرگز به‌صورت «کدی نیست» یا یک عدد گزارش نمی‌شود. فقط قطع
اتصال (`AdapterConnectionError`) کل گزارش را متوقف می‌کند.
```

جریان داشبورد (نشست پایدار، روی همان قرارداد):

```
app.gui_main → load_settings + configure_logging → AppDashboardBackend
  └─ ui.MainWindow(backend)
       └─ backend.create_session(simulation, port)   ← شبیه‌سازی: mock + drive_cycle
            │                                           واقعی: elm327 روی درگاه انتخاب‌شده (بدون درگاه: ConfigurationError)
            └─ diagnostics.LiveDataSession
                 ├─ connect()                         ← خطای اتصال = خطا، بدون جایگزینی با شبیه‌ساز
                 ├─ poll(DASHBOARD_PIDS)  → LiveSnapshot (وضعیت جدا برای هر PID، بدون مقدار جایگزین)
                 ├─ read_trouble_codes()  → TroubleCode[] (شکست = خطا، نه «کدی نیست»)
                 ├─ read_vehicle_info()   → VehicleInfo (با simulated)
                 └─ request_clear_trouble_codes()  → گارد ایمنی آداپتور → SafetyGuardError
```

نکات طراحی:

- آداپتور **خام** برمی‌گرداند (کد خطا به‌صورت رشته)؛ تفسیر و توضیح کار لایه تشخیص است.
- اینکه داده شبیه‌سازی‌شده است، از `AdapterInfo.simulated` می‌آید که صریح اعلام می‌شود، نه از
  نوع آداپتور. این پرچم در گزارش، `LiveSnapshot`، CSV و رابط کاربری حمل می‌شود.
- PID پشتیبانی‌نشده استثنا پرتاب می‌کند؛ در خواندن گروهی به `skipped_pids` تبدیل می‌شود تا
  در گزارش دیده شود (نه پنهان).
- توضیح نبودن برای یک کد خطا یک نتیجه معتبر است (`description unknown`)، نه خطا.

## ۴. مدل خطاها

| استثنا | معنا | مسیر بروز |
|---|---|---|
| `ConfigurationError` | تنظیمات ناقص/نامعتبر/کلید ناشناخته | بارگذاری تنظیمات، CLI |
| `AdapterNotFoundError` | نام آداپتور ناشناخته | رجیستری |
| `AdapterNotImplementedError` | آداپتور اعلام‌شده ولی پیاده‌نشده | رجیستری |
| `AdapterConnectionError` | نبود اتصال / امتناع اتصال | آداپتور |
| `AdapterTimeoutError` | پاسخ ندادن در زمان مقرر | آداپتور |
| `ProtocolError` | پاسخ دریافت شد ولی قابل تفسیر نبود | آداپتور |
| `UnsupportedPidError` | PID پشتیبانی‌نشده | آداپتور |
| `UnsupportedOperationError` | قابلیت معتبر ولی پیاده‌نشده | سرویس ۰۴، آداپتورها |
| `SafetyGuardError` | رد شدن عملیات توسط سیاست ایمنی | `core.safety.require()` |
| `KnowledgeDatabaseError` | داده دانش ناخوانا/نامعتبر | `database.repository` |

قاعده: هر خطای عمدی پروژه از `IranObdTunerError` ارث می‌برد تا CLI/UI بتواند خطای برنامه را
از باگ برنامه‌نویسی تشخیص دهد.

## ۵. مرز ایمنی (Safety boundary)

```python
class OperationClass(StrEnum):
    READ_ONLY = "read_only"  # همیشه مجاز
    STATE_CHANGING = "state_changing"  # مثلاً پاک کردن DTC (سرویس ۰۴) — ممنوع
    CALIBRATION_WRITE = "calibration_write"  # نوشتن/فلش کالیبراسیون — ممنوع


DEFAULT_POLICY = SafetyPolicy(allowed={READ_ONLY})
```

- `core.safety.require(...)` تنها دروازه است؛ `ObdAdapter.clear_diagnostic_trouble_codes()`
  (که داشبورد از طریق `LiveDataSession.request_clear_trouble_codes()` به آن می‌رسد) و
  `tuning.interfaces.assert_write_allowed()` از آن عبور می‌کنند.
- سیاست ایمنی **از تنظیمات/محیط خوانده نمی‌شود**؛ فقط در کد ساخته می‌شود. یک فایل تنظیمات
  اشتباه نمی‌تواند نوشتن روی ECU را فعال کند.
- در آداپتور شبیه‌ساز، حتی با سیاست اجازه‌دهنده هم پیاده‌سازی وجود ندارد:
  `UnsupportedOperationError`.
- **مسیر پاک کردن کدهای خطا در داشبورد:** دکمه «پاک کردن کدها» درخواست را به
  `ObdAdapter.clear_diagnostic_trouble_codes()` می‌فرستد که با سیاست پیش‌فرض
  `SafetyGuardError` و با هر سیاست دیگری `UnsupportedOperationError` می‌دهد. رابط کاربری یک
  پنجره توضیح (غیرمسدودکننده) نشان می‌دهد و رویداد را در لاگ ثبت می‌کند. **هیچ پیاده‌سازی
  سرویس ۰۴ در کد وجود ندارد.** کد python-OBD قبلی (`ObdService.clear_dtc`) در Milestone 1 با
  تأیید صاحب پروژه حذف شد. `tests/unit/test_architecture.py` شکست می‌خورد اگر `CLEAR_DTC`
  دوباره ظاهر شود یا کلاسی این متد را override کند.
- **UDS روی CAN (Milestone 4):** `hardware/uds.check_request_allowed` تنها دروازه بین کلاینت و باس
  است و پیش‌فرض‌-رد است: فقط `0x10` با ۰۱/۰۳ (و نسخه suppress آن‌ها)، `0x3E` ۰۰/۸۰، `0x22` با
  دقیقاً یک DID و `0x19` ۰۱/۰۲ مجازند. `FORBIDDEN_SERVICES` سرویس‌های نوشتن/امنیت/ریست/برنامه‌ریزی را
  با نام رد می‌کند. جلسه Extended فقط با پارامتر کدی `allow_extended_session=True` (چک‌باکس رابط کاربری
  یا `--extended-session` در CLI) ممکن است، در تنظیمات وجود ندارد و `extended_session()` همیشه در
  `finally` به `0x10 0x01` برمی‌گردد. جستجوی تابعی (`3E 00` روی `0x7DF`) هم از همین دروازه عبور
  می‌کند. ECU شبیه‌سازی‌شده هم هر سرویس دیگری را با `serviceNotSupported` جواب می‌دهد. تست‌های
  `test_uds.py` (همه ۶۵۵۳۶ ترکیب SID/زیرتابع) و `test_architecture.py` (نبود literal درخواست با SID
  نوشتنی، نبود نام این سرویس‌ها بیرون از `uds.py`، صریح بودن `interface=` در `can.Bus`، import
  تنبل `can`/`isotp`/`udsoncan` فقط در `hardware`) این را قفل می‌کنند.
- پیش‌نیازهای هر نوشتن آینده در `BenchSafetyChecklist` (پشتیبان‌گیری، تأیید خواندن مجدد
  پشتیبان، پایداری ولتاژ، چک‌سام مستند، تناسب با ECU، اعتبارسنجی خودرویی، تأیید صاحب پروژه)
  آمده است.
- **این پروژه هیچ عملیات فلش، دور زدن Security Access یا فرمان مخرب ECU را پیاده نمی‌کند و در
  setup/تست/CI هیچ فرمانی به خودرو ارسال نمی‌شود.** هیچ تستی پورت سریال باز نمی‌کند: فهرست
  درگاه‌ها فقط سیستم‌عامل را پرس‌وجو می‌کند و در تست با ماژول pyserial جعلی جایگزین می‌شود.

هر قابلیت نوشتنی در آینده نیازمند: تأیید صریح کارفرما/تیم، شواهد مستند برای خانواده ECU هدف
(الگوریتم چک‌سام، طرح Security Access، چیدمان حافظه)، تست کنترل‌شده روی ECU آزمایشگاهی و
به‌روزرسانی جدول‌های قابلیت در README است.

## ۶. سازگاری سخت‌افزار و پروتکل

- آداپتورها در `hardware/registry.py` اعلام می‌شوند؛ ستون `implemented` صریح است.
- `AdapterInfo.protocols` فقط پروتکل‌هایی را فهرست می‌کند که سازنده/مستندات ادعا کرده‌اند؛
  فهرست خالی یعنی «نامعلوم»، نه «همه چیز».
- PID پشتیبانی‌شده از خود خودرو پرسیده می‌شود (`read_supported_pids`)، نه از فرض پروژه.
  آداپتورهای واقعی باید این را از بیت‌ماسک‌های `0100/0120/0140` استخراج کنند.
- محدودیت‌های شناخته‌شده مکتوب‌اند: کلا ن‌های ELM327 ارزان ممکن است بخشی از پروتکل‌ها را
  نداشته باشند؛ CAN لازم برای کار Bench با ELM327 ممکن نیست.

## ۷. دیتابیس دانش

- قرارداد: `KnowledgeRepository` با `describe_dtc()`, `source`, `metadata()`.
- پیاده‌سازی فعلی: `JsonKnowledgeRepository` — کاتالوگ همراه بسته + پوشه override
  (`knowledge_dir` در تنظیمات) که ورودی‌هایش جای کاتالوگ همراه را می‌گیرد.
- متادیتای غیرمرجع بودن (`authoritative: false`) در گزارش‌ها حمل می‌شود تا هیچ‌کس توضیحات
  کد خطا را به‌عنوان مرجع قطعی تعبیر نکند.
- جایگزینی آینده (SQLite، پارس A2L/DAMOS) فقط با تعریف وابستگی جدید و تأیید تیم انجام می‌شود.

## ۸. نقاط توسعه

**افزودن PID جدید:** ورودی به `core/pids.py` (`PID_CATALOG`) با شناسه در فرم درخواست، نام،
واحد و توضیح؛ سپس در صورت نیاز به لیست پیش‌فرض Live Data در `core/pids.py` و
`config/example.toml` اضافه شود. برای PIDهای اختصاصی سازنده، اول شواهد، بعد ورودی.

**افزودن آداپتور واقعی:** کلاس جدید در `hardware/<name>.py` که `ObdAdapter` را پیاده کند،
import کتابخانه خارجی داخل همان ماژول، `implemented=True` در رجیستری، به‌روزرسانی جدول‌های
قابلیت و افزودن یک تست با پاسخ‌های ساختگی (بدون سخت‌افزار).

**افزودن قابلیت گرافیکی:** ویجت یا صفحه جدید در `ui/` و رشته‌هایش در `ui/i18n.py` (هر دو زبان).
گرافیک باید از `ui/__init__.py` قابل import نباشد (تا تست‌ها و CLI بدون PySide6 کار کنند) و در
`tests/integration/test_gui_window.py` با پلتفرم `offscreen` تست شود.

**افزودن داده دانش:** یک `dtc_catalog.json` در پوشه‌ای خارج از مخزن (یا `knowledge/local/`)
و تنظیم `knowledge_dir`. برای انتشار در بسته، ورودی‌ها را با ذکر منبع به
`src/iran_obd_tuner/database/data/dtc_catalog.json` اضافه کنید.

**افزودن قابلیت نوشتنی:** مجاز نیست تا زمانی که بخش ۵ برآورده شود؛ در آن صورت رابط در
`tuning/interfaces.py` پیاده و همه مسیرها از `assert_write_allowed()` عبور کنند.

## ۹. تصمیم‌های معماری (ADRs کوتاه)

| # | تصمیم | دلیل | جایگزین رد‌شده |
|---|---|---|---|
| ۱ | src-layout با بسته `iran_obd_tuner` | جلوگیری از تداخل نام ماژول‌های عمومی، نصب قابل‌اتکا، تست بدون نصب | پوشه‌های تخت `src/ui`, `src/core` |
| ۲ | هسته بدون وابستگی خارجی | اجرا/تست بدون خودرو، GUI و شبکه؛ حذف ریسک وابستگی‌های سنگین | افزودن ORM/فریمورک از ابتدا |
| ۳ | extras به‌جای وابستگی اجباری (`obd`/`can`/`bench`/`gui`) | حفظ وابستگی‌های موجود فعلی بدون تحمیل نصب سنگین در CI | یک `requirements.txt` غول |
| ۴ | دیتاست TOML + متغیر محیطی با رد کردن کلید ناشناخته | خطای تایپی زودتر دیده می‌شود؛ فایل محلی از مخزن بیرون می‌ماند | YAML/JSON بدون اعتبارسنجی |
| ۵ | سیاست ایمنی فقط در کد (نه در تنظیمات) | یک فایل پیکربندی نباید بتواند ECU را قابل‌نوشتن کند | پرچم `allow_writes` در TOML |
| ۶ | آداپتور شبیه‌ساز به‌عنوان آداپتور پیش‌فرض | توسعه و تست بدون سخت‌افزار، با تزریق خطای ارتباطی | mock دست‌ساز داخل تست‌ها |
| ۷ | ruff به‌عنوان تنها ابزار لینت و فرمت | یک ابزار، یک پیکربندی، سرعت بالا | black + flake8 + isort |
| ۸ | نسخه فقط در `src/iran_obd_tuner/__init__.py` | یک منبع حقیقت؛ `pyproject` آن را داینامیک می‌خواند | نسخه تکراری در دو فایل |
| ۹ | انتقال کد فاز ۱ با `git mv` بدون بازنویسی منطق | کار همکار حفظ می‌شود، تاریخچه فایل‌ها می‌ماند، diff قابل بازبینی است | بازنویسی UI روی `DiagnosticService` در همین PR |
| ۱۰ | ~~عبور دادن `ObdService.clear_dtc()` از گارد ایمنی و غیرقابل‌دسترس کردن مسیر واقعی~~ — **جایگزین شد با ADR ۱۲** | | |
| ۱۱ | تست گرافیکی روی پلتفرم `offscreen` + `importorskip` | مجموعه تست بدون نمایشگر/سخت‌افزار اجرا می‌شود و با نصب PySide6 واقعاً GUI را تست می‌کند | وابسته کردن کل تست‌ها به Qt |
| ۱۲ | (Milestone 1) حذف `ObdService` و هر پیاده‌سازی سرویس ۰۴؛ داشبورد روی `LiveDataSession` + `ObdAdapter` | یک مسیر داده، یک مدل (`Measurement`)، یک مرز ایمنی؛ قابلیت نوشتن پنهان باقی نمی‌ماند | نگه داشتن مسیر python-OBD پشت policy |
| ۱۳ | (Milestone 1) `ui` فقط یک Protocol (`DashboardBackend`) می‌شناسد و `app` آن را پیاده می‌کند | UI آداپتور انتخاب نمی‌کند، تست‌ها backend دلخواه می‌دهند، `ui → app` ایجاد نمی‌شود | ساخت آداپتور داخل `MainWindow` |
| ۱۴ | (Milestone 1) هیچ مقدار جایگزین: هر PID وضعیت دارد و خانه CSV خالی می‌ماند | عدد ساختگی (مثل ۱۲٫۶ ولت یا صفر) قابل تشخیص از داده واقعی نیست | مقدار پیش‌فرض برای PID ناموفق |
| ۱۶ | (Milestone 2) درایور ELM327 خودِ پروژه روی pyserial به‌جای python-OBD | کنترل کامل timeout و خطاهای نوع‌دار، تست قطعی با transcript، حذف `pint`؛ python-OBD خطاها را می‌بلعد و قدیمی است | python-OBD پشت `ObdAdapter` |
| ۱۷ | (Milestone 2) فهرست سفید دستور در نقطه واحد ارسال + `AT TP 0` به‌جای `AT SP 0` | دفاع چندلایه: حتی باگ در کد بالاتر نمی‌تواند سرویس ۰۴ یا UDS بفرستد؛ EEPROM آداپتور نوشته نمی‌شود | اتکا فقط به گارد `core.safety` |
| ۱۸ | (Milestone 2) بدون جستجوی خودکار درگاه؛ قطع اتصال اگر دستگاه به `ATZ` به‌عنوان ELM327 پاسخ ندهد | ارسال فرمان به دستگاه ناشناخته روی درگاه دیگر خطرناک است | امتحان همه درگاه‌ها |
| ۱۹ | (Milestone 2) همه I/O آداپتور روی یک thread پس‌زمینه، به ترتیب؛ نتیجه‌های کهنه پس از قطع دور ریخته می‌شوند | جستجوی پروتکل تا ده‌ها ثانیه طول می‌کشد؛ session ایمن برای چند thread نیست | thread pool |
| ۲۰ | (Milestone 3) خواندن مستقل هر بخش با `ReadOutcome`؛ گزارش JSON نسخه ۲ | پشتیبانی جزئی ECUها (مثلاً بدون سرویس ۰A) رایج است؛ نبود یک بخش نباید کل تشخیص را خراب کند | شکست کل گزارش با اولین خطا |
| ۲۱ | (Milestone 3) فهرست سفید فقط `02xx00` (frame ۰۰)، `07` و `0A` را اضافه می‌کند | همه خواندنی‌اند؛ ۰۴، ۰۸ و UDS همچنان رد می‌شوند | باز کردن کل سرویس ۰۲ |
| ۲۲ | (Milestone 3) `TrafficLog` در `core` (نه `hardware`) و تزریق آن از `app` به آداپتور | آداپتور در thread کاری می‌نویسد و UI در thread اصلی می‌خواند، بدون اینکه `ui` لایه `hardware` را import کند. فقط دستورهای عبوری از فهرست سفید ثبت می‌شوند | ثبت در LiveDataSession (پاسخ خام را نمی‌بیند) |
| ۲۳ | (Milestone 3) شبیه‌ساز در ترافیک فقط «درخواست → مقدار decodeشده» ثبت می‌کند | شبیه‌ساز باس ندارد؛ فریم هگز ساختگی شبیه ترافیک واقعی، داده جعلی است | تولید فریم با فرمول معکوس |
| ۲۴ | (Milestone 3) شتاب دینو از مشتق دور موتور در یک دنده، نه از سرعت | سرعت دقت ۱ km/h دارد و مشتقش نویز است؛ rpm دقت ۰٫۲۵ دارد. `k` با کمترین مربعات از زوج‌های (rpm، سرعت) | مشتق مستقیم سرعت |
| ۲۵ | (Milestone 3) هر `PidReading` زمان monotonic خودش را دارد (`sampled_at_s`) | پارامترهای یک poll در زمان‌های متفاوت خوانده می‌شوند | زمان کلی snapshot |
| ۲۶ | (Milestone 3) وزن خالی خودرو برای دینو پیش‌فرض ندارد؛ بقیه پارامترها پیش‌فرض عمومی با برچسب | وزن هیچ خودرویی را از خودمان نمی‌سازیم | پیش‌فرض برای پژو پارس |
| ۲۷ | (Milestone 4) ISO-TP خودِ پروژه به‌جای `can-isotp` | منطق کوچک و کاملاً قابل‌تست با باس جعلی و ساعت تزریقی؛ بدون thread پس‌زمینه کتابخانه؛ ترافیک فریم‌به‌فریم ثبت می‌شود؛ یک وابستگی کمتر | `can-isotp` (رفتار thread و timeout آن کمتر قابل‌کنترل است) |
| ۲۸ | (Milestone 4) کلاینت UDS فقط‌خواندنی خودِ پروژه؛ `udsoncan` در `[bench]` دست‌نخورده | `udsoncan` همه سرویس‌های نوشتنی را دارد؛ فهرست سفید یک‌جا و قابل AST-تست ساده‌تر از محدود کردن یک کتابخانه کامل است | `udsoncan` پشت wrapper |
| ۲۹ | (Milestone 4) فهرست سفید پیش‌فرض‌-رد + فهرست نام‌دار سرویس‌های ممنوع، فقط در `hardware/uds.py` | دفاع چندلایه مانند ADR ۱۷؛ حتی باگ در لایه بالاتر نمی‌تواند `0x27`/`0x2E`/`0x34` بفرستد | فقط گارد `core.safety` |
| ۳۰ | (Milestone 4) جلسه Extended: opt-in کدی برای هر اجرا، نه تنظیمات؛ همیشه بازگشت به default | بعضی DIDها فقط در `0x03` خوانده می‌شوند، ولی تغییر جلسه نباید با یک فایل یا متغیر محیطی دائمی شود | کلید `allow_extended_session` در TOML |
| ۳۱ | (Milestone 4) `virtual-sim` = باس `virtual` python-can + سرور ECU شبیه‌سازی؛ رابط واقعی بدون کانال خطا می‌دهد و هرگز به شبیه‌ساز برنمی‌گردد | داده شبیه‌سازی از منبع (لینک) برچسب می‌خورد، نه از مقدار | حدس شبیه‌سازی از مقدار VIN |
| ۳۲ | (Milestone 4) خروجی `uds-ident` یک سند JSON جدا (`kind: uds_ecu_identification`، `schema_version: 1`)؛ `DiagnosticReport` نسخه ۲ دست‌نخورده | گزارش OBD و شناسایی UDS دو جریان مستقل‌اند | افزودن بخش UDS به گزارش و افزایش نسخه |
| ۳۳ | (Milestone 4) `VehicleInfo.vin_source` (`obd_09` / `uds_f190`) | منبع VIN باید قابل ردیابی باشد. این فیلد به `vehicle` در گزارش نسخه ۲ **اضافه** شده است (افزودنی؛ کلید قبلی حذف یا تغییر نکرده)، پس نسخه افزایش نیافت | نسخه ۳ برای یک کلید اختیاری |
| ۱۵ | (Milestone 1) `scripts/check.py` ابزار غایب را شکست حساب می‌کند (`--allow-missing` برای حالت نرم) | گیت skip‌شده گیت پاس‌شده نیست؛ از سبز کاذب جلوگیری می‌کند | skip خاموش |

### بدهی فنی (Known debt)

سه ناهمگونی که در ادغام فاز ۱ ثبت شده بود (دو مسیر خواندن، `SAMPLE_DTC` تکراری، `LiveSample`
بدون واحد و زمان) در **Milestone 1 برطرف شدند** (ADR ۱۲ تا ۱۴). بدهی باقی‌مانده:

1. ~~**خواندن روی thread اصلی GUI**~~ — **برطرف شد (Milestone 2، ADR ۱۹).** بستن پنجره در میانه جستجوی
   پروتکل تا پایان همان درخواست (حداکثر حدود ۴۵ ثانیه) منتظر می‌ماند.
2. **`VehicleInfo.protocol`** از اولین پروتکل اعلام‌شده در `AdapterInfo.protocols` پر می‌شود، نه
   از پروتکل مذاکره‌شده. — **برطرف شد (Milestone 2):** `ObdAdapter.active_protocol`.
3. **CI تست‌های GUI را اجرا نمی‌کند** (`[gui]` در CI نصب نمی‌شود). اگر یک job جدا با
   PySide6 و `QT_QPA_PLATFORM=offscreen` اضافه شود، کهنه شدن تست‌های GUI دیده می‌شود.
4. **پالت رنگ** در `gauges.py`، `main_window.py` و `safety_modal.py` تکرار شده است و آیکون هنوز
   پالت آبی قدیمی را دارد. فونت `Vazirmatn-Regular.ttf` در مخزن نیست، پس همیشه فونت جایگزین
   بارگذاری می‌شود.
5. **وابستگی‌ها:** `pyqtgraph` در `[gui]` تعریف شده ولی هیچ جا import نمی‌شود. (`requirements.txt` در Milestone 2
   با `pyproject.toml` هم‌خوان شد.)
6. **توضیح کدهای خطا** فقط انگلیسی است. کاتالوگ فارسی نیازمند منبع مستند است.
7. **ELM327 با headerهای خاموش** پاسخ چند ECU را از هم تشخیص نمی‌دهد: برای هر مقدار اولین پاسخ
   استفاده می‌شود و bitmaskها با هم ترکیب می‌شوند. پاسخ چندفریمی درهم رد می‌شود.
8. **ELM327 روی سخت‌افزار واقعی اعتبارسنجی نشده است.** timeoutهای `RESET_TIMEOUT_S` و `SEARCH_TIMEOUT_S`
   برآورد محافظه‌کارانه‌اند و رفتار کلون‌های ارزان ممکن است با datasheet فرق کند.
9. **جدول بیت‌های PID `0101`** از خلاصه‌های عمومی J1979 آمده و با متن استاندارد تأیید نشده است؛
   بیت ۴ بایت‌های C/D بین نسخه‌های J1979 فرق دارد و با نام خنثی `bit4_ac_or_gpf` گزارش می‌شود.
   ترکیب پاسخ چند ECU (MIL = OR، تعداد = جمع، مانیتورها از اولی) پیشنهاد محافظه‌کارانه است.
10. **پاسخ `7F xx 78` (Response Pending)** اگر از آداپتور عبور کند خطا گزارش می‌شود؛ نسخه‌های قدیمی
    ELM327 (مثل v1.3a) خودشان منتظر پاسخ نهایی نمی‌مانند.
11. **دقت دینوی مجازی فقط از نظر عددی تأیید شده است:** تست، یک خودروی مرجع با همان مدل
    فیزیکی را شبیه‌سازی و مانند ELM327 نمونه‌برداری می‌کند (۱ تا ۸ هرتز، سرعت گردشده به ۱ km/h)؛
    قله توان و گشتاور با خطای کمتر از ۱٪ بازیابی می‌شود. این ثابت می‌کند هموارسازی و
    کوانتیزه شدن مشکلی ایجاد نمی‌کنند، **نه** اینکه نتیجه روی خودروی واقعی درست است: خطای
    وزن، Cd، سطح مقطع، تلفات انتقال، شیب جاده و باد مستقیماً وارد نتیجه می‌شود.
12. **زمان‌سنج ۰ تا ۱۰۰** از سرعتی که ECU گزارش می‌دهد استفاده می‌کند (معمولاً کمی بیشتر از
    سرعت واقعی) و دقتش به نرخ نمونه وابسته است؛ عدم‌قطعیت همراه نتیجه گزارش می‌شود.
13. پارامترهای دینو از `dyno_*` در `config/local.toml` خوانده می‌شوند و تغییرشان در UI فقط
    برای همان جلسه است (برنامه فایل تنظیمات را بازنویسی نمی‌کند).
14. **CAN/ISO-TP/UDS روی رابط و ECU واقعی اعتبارسنجی نشده است.** همه تست‌ها روی باس جعلی یا باس
    مجازی python-can با ECU شبیه‌سازی اجرا می‌شوند. timeoutها (`p2_s = 1 s`، `p2_star_s = 5 s`،
    `N_Bs/N_Cr = 1 s`) برآورد محافظه‌کارانه‌اند.
15. **پژو پارس TU5:** معلوم نیست ECU این خودرو روی پایه‌های CAN سوکت OBD (۶ و ۱۴) UDS پاسخ دهد؛
    ممکن است فقط K-Line (ISO 14230/KWP2000) داشته باشد. شناسه‌های پیش‌فرض `0x7E0/0x7E8` از
    ISO 15765-4 هستند و **برای هیچ خودروی ایرانی تأیید نشده‌اند**؛ از «جستجوی ECU» استفاده کنید.
16. فقط آدرس‌دهی ۱۱ بیتی و فریم کلاسیک ۸ بایتی پیاده شده است (نه ۲۹ بیتی، نه CAN FD، نه آدرس‌دهی
    extended/mixed ISO-TP). جستجوی تابعی فقط پاسخ‌های Single Frame را جمع می‌کند.
17. decode DTC در UDS فقط برای DTCFormatIdentifier `0x00`/`0x04` (SAE J2012-DA) انجام می‌شود؛
    قالب‌های دیگر به‌صورت هگز خام نمایش داده می‌شوند. توضیح کدهای UDS از دیتابیس دانش خوانده نمی‌شود.
18. هر عملیات UDS در داشبورد لینک را باز و بسته می‌کند (one-shot)؛ برای رابط‌های سریال مثل `slcan`
    این یعنی باز شدن مجدد درگاه در هر کلیک. نگه داشتن لینک و TesterPresent دوره‌ای پیاده نشده است.

### تصمیم‌هایی که نیازمند تأیید تیم است

1. **کف نسخه پایتون (`>=3.11`)** — بر پایه دسترس بودن `tomllib` و تایپینگ مدرن انتخاب شده و
   روی ۳٫۱۲ و ۳٫۱۳ تست شده است؛ اگر یکی از اعضا روی ۳٫۹/۳٫۱۰ کار می‌کند باید تأیید/تغییر شود.
2. ~~**نام درست پکیج `obd` روی PyPI**~~ — **دیگر مطرح نیست (Milestone 2):** python-OBD استفاده نمی‌شود و
   از extraها حذف شد (ADR ۱۶).
3. **جابه‌جایی پوشه‌ها** از `src/<module>/` به `src/iran_obd_tuner/<module>/` (ADR ۱ و ۹) — اگر
   روی شاخه‌ای کد محلی در مسیرهای قبلی وجود دارد، پیش از merge هماهنگ شود.
4. **فایل `LICENSE` (MIT)** — README از قبل مجوز MIT را اعلام کرده بود ولی فایل مجوز وجود
   نداشت؛ متن MIT با دارندهٔ حق «Iran OBD Tuner contributors» اضافه شد. نام قانونی مالک
   پروژه باید تأیید و در صورت نیاز اصلاح شود.
5. **`requirements.txt` در کنار `pyproject.toml`** — نسخه‌های موجود دست‌نخورده حفظ شدند، در
   حالی که extras در `pyproject.toml` تعریف شده‌اند. اگر این دوگانگی ناخواسته است، یکی حذف شود.
6. ~~**پاک کردن کدهای خطا (سرویس ۰۴)**~~ — **تصمیم گرفته شد (Milestone 1):** مسیر python-OBD
   حذف شد. هر پیاده‌سازی آینده نیازمند تأیید صریح، سیاست کدی مخصوص، تأیید چندمرحله‌ای در UI و
   به‌روزرسانی جدول قابلیت‌هاست (CLAUDE.md بخش ۶).
7. ~~**یکسان‌سازی سرویس داشبورد با `ObdAdapter`**~~ — **انجام شد (Milestone 1)**، ADR ۱۲.
8. ~~**پایه آداپتور ELM327 در Milestone 2**~~ — **تصمیم گرفته شد:** درایور خود پروژه روی pyserial
   (ADR ۱۶). بسته `obd` از extraها حذف شد.

## ۱۰. خارج از محدوده این نسخه

- آداپتور CAN مستقیم، ISO-TP و UDS (`python-can`, `can-isotp`, `udsoncan`) — فاز ۲
- هر نوع نوشتن/فلش/Security Access
- نرمال‌سازی/دیتابیس ECU و نقشه‌ها (A2L/DAMOS)
- ضبط بلندمدت Live Data، چند-خودرویی، شبکه/همگام‌سازی ابری
