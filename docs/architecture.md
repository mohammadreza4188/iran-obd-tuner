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
tuning/        تحلیل آفلاین ROM + مرز ایمنی نوشتن روی ECU      ← core
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
  شبیه‌سازی بیرون از `hardware/uds_link.py`. (Milestone 5) `vehicle_matcher.py` و
  `evidence_export.py` از `hardware` فقط `PROTOCOL_NAMES` را می‌گیرند (هیچ کلاینت یا آداپتوری)،
  `database` هیچ فایلی نمی‌نویسد، و فهرست‌های سفید UDS/ELM327 گسترش نیافته‌اند. (Milestone 6) `tuning`
  فقط `core` و کتابخانه استاندارد بی‌خطر (بدون شبکه/subprocess/ctypes) را import می‌کند، فقط
  `tuning/rom_export.py` فایل می‌نویسد، `ui` هرگز `tuning` را import نمی‌کند و هیچ `write_calibration`
  پیاده‌سازی نمی‌شود.

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
| `hardware/uds_sim_programmable.py` | (Milestone 7b) `VirtualProgrammableEcu`: سمت ECU شبیه‌سازی‌شده برنامه‌ریزی Bench (فلش NOR، bootloader، seed-key با شمارنده تلاش و تأخیر، erase/checkMemory، دانلود/آپلود بلوکی)، `FaultPlan`، `SimulatedXorSeedKey`، `VirtualProgrammableEcuServer` (فقط باس `virtual`) و از 7e `SimulatedEcuState` (حالت پایدار برای راه‌اندازی دوباره) | پیاده‌شده (شبیه‌سازی) |
| `hardware/bench_programmer.py` | (Milestone 7c) `BenchProgrammer`: **تنها ماژولی که درخواست برنامه‌ریزی می‌سازد**؛ فهرست سفید Bench (`check_bench_request`)، حلقه درخواست/پاسخ مستقل از `uds.py`، بررسی capability در اولین دستور هر متد، تکرار محدود `0x36` | پیاده‌شده (فقط شبیه‌ساز) |
| `hardware/bench_link.py` | (Milestone 7c) `BenchLink`/`BenchLinkConfig`: فقط `virtual-sim-programmable` (باس virtual + ECU برنامه‌پذیر شبیه‌سازی‌شده)؛ هر رابط یا کانال دیگری رد می‌شود | پیاده‌شده (فقط شبیه‌ساز) |
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
| `core/knowledge.py` | `ConfidenceLevel`، `Source`، `Claim`، `ObdProtocolId`، پروفایل خودرو/ECU، `claim_rows` | پیاده‌شده |
| `database/vehicle_schema.py`, `database/vehicles.py` | اعتبارسنج سخت‌گیر بسته‌ها + `JsonVehicleKnowledgeBase` (همراه + محلی) | پیاده‌شده |
| `database/data/vehicles/*.json` | بسته‌های ایران‌خودرو، سایپا، ام‌وی‌ام + `guidance.json` دوزبانه | پیاده‌شده (ادعاهای باس/ECU همه «نامعلوم») |
| `diagnostics/vehicle_matcher.py` | `ObservedFacts`، `rank_candidates`، نرمال‌سازی پروتکل | پیاده‌شده (غیرفعال) |
| `diagnostics/evidence_export.py` | بسته شواهد میدانی: پنهان‌سازی VIN، هش، ردیف‌های ارتقا | پیاده‌شده |
| `ui/knowledge_view.py` | تب «پایگاه دانش خودرو» | پیاده‌شده |
| `tuning/interfaces.py` | `CalibrationReader/Writer`، `BenchSafetyChecklist` (از 7d: `HUMAN_ITEMS`/`MACHINE_ITEMS` و `compose(attestation, machine)`)، `BenchMachineChecks` | قرارداد؛ `CalibrationWriter` همچنان پیاده نشده |
| `core/calibration.py` | نوع داده، دسته نقشه، الگوریتم/وضعیت چک‌سام، snapshotهای فقط‌خواندنی برای CLI/UI | پیاده‌شده |
| `tuning/rom_loader.py` | `load_rom` (۱۶KiB–۸MiB، رد HEX/SREC)، `RomImage` تغییرناپذیر، رشته‌های ASCII، آمار پرشدگی | پیاده‌شده |
| `tuning/map_definitions.py` | اعتبارسنج فایل تعریف نقشه (منبع، `verified` ممنوع) + `check_compatibility` | پیاده‌شده |
| `tuning/tables.py`, `tuning/workspace.py` | decode/encode محدود، فرمان‌های Undo/Redo، diff، `RomWorkspace` | پیاده‌شده |
| `tuning/checksum.py` | هفت الگوریتم با تعریف دقیق، بازه و حذف فیلد خودِ چک‌سام | پیاده‌شده |
| `tuning/rom_export.py` | خروجی غیرمخرب + مانیفست (یکی از سه نویسنده فایل در `tuning`) | پیاده‌شده |
| `tuning/flash_plan.py` | (Milestone 7d) `build_flash_plan`: اندازه، ناحیه‌ها، یکپارچگی تصویر، چک‌سام `valid` (override فقط برای target شبیه‌سازی‌شده) | پیاده‌شده |
| `tuning/flash_gate.py` | (Milestone 7d) **تنها سازنده `FlashAuthorization`**: سیاست `BENCH_FLASH`، attestation کامل، فقط شبیه‌سازی، بررسی‌های ماشینی پوشه پشتیبان | پیاده‌شده (فقط شبیه‌ساز) |
| `tuning/backup_vault.py` | (Milestone 7d) **تنها سازنده `BackupReceipt`**: دو خواندن برابر، نوشتن یک‌باره (`xb`)، مانیفست، بازخوانی از دیسک | پیاده‌شده |
| `tuning/flash_journal.py` | (Milestone 7d) ژورنال اتمیک تراکنش، قفل تک‌نمونه، `JournalInspector` و `RecoveryAssessment`، بایگانی | پیاده‌شده |
| `tuning/flash_orchestrator.py` | (Milestone 7d) `BenchFlashOrchestrator`: چهار مرحله و بازیابی، فقط روی `BenchProgrammerPort` | پیاده‌شده (فقط شبیه‌ساز) |
| `core/bench_reports.py` | (Milestone 7e) گزارش‌های ساده و بدون Qt جلسه Bench برای CLI و UI: `BenchJournalReport` (`clean`/`stale`/`dirty`/`corrupt`)، `BenchFlashReport`، `SimulatedFault` | پیاده‌شده |
| `tuning/demo.py`, `tuning/data/demo_maps.json` | ROM نمایشی شبیه‌سازی‌شده و تعریف نقشه‌هایش | پیاده‌شده (شبیه‌سازی) |
| `ui/bench_flash_panel.py` | (Milestone 7f) `BenchFlashPanel`، `BenchAttestationDialog`، `JournalDialog`: فلش و بازیابی شبیه‌سازی‌شده در تب Bench، فقط روی Protocol `BenchFlashService` | پیاده‌شده (فقط شبیه‌ساز) |
| `app/bench.py`, `ui/bench_view.py` | `AppBenchWorkspace` (پیاده‌سازی Protocol `BenchWorkspace`) و تب «میز کار ROM» | پیاده‌شده |
| `app/bench_flash.py` | (Milestone 7e) `BenchFlashService`: **تنها نقطه اتصال** هماهنگ‌کننده `tuning` به `hardware/bench_link.py`؛ `assess_journal`، `run_simulated_flash`، `run_simulated_recovery`، `discard_stale_journal`؛ ذخیره حالت پایدار ECU شبیه‌سازی‌شده بین اجراها | پیاده‌شده (فقط شبیه‌ساز) |
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
- **میز کار ROM آفلاین (Milestone 6):** تحلیل فایل، نه عملیات روی ECU. تصویر بارگذاری‌شده `bytes`
  تغییرناپذیر است و ویرایش روی یک کپی کاری انجام می‌شود. `tuning/rom_export.py` تنها نویسنده فایل در
  `tuning` است (تست AST) و خروجی را رد می‌کند اگر: مقصد همان فایل اصلی باشد (هر املایی، symlink یا
  hardlink)، تأیید کاربر نباشد، چیزی تغییر نکرده باشد، چک‌سام تعریف‌شده نامعتبر باشد، بایتی بیرون از
  جدول‌ها و فیلدهای چک‌سام تغییر کرده باشد یا فایل اصلی روی دیسک عوض شده باشد. کنار هر خروجی یک
  مانیفست با `validated_for_vehicle: false` نوشته می‌شود. «معتبر» برای چک‌سام فقط یعنی مطابق الگوریتم
  تعریف‌شده. هیچ کلاسی `write_calibration` را پیاده نمی‌کند و `DEFAULT_POLICY` همچنان
  `CALIBRATION_WRITE` را رد می‌کند (تست معماری).
- **سخت‌سازی پیش از آزمون میدانی (ADR ۴۷ تا ۵۳):** هیچ انتظاری در مسیر سخت‌افزار نامحدود نیست و
  هیچ خطایی به تکرار خودکار درخواست روی باس خودرو منجر نمی‌شود:
  - **سریال:** `PySerialTransport` با `write_timeout = 1 s` باز می‌شود و `flush()` pyserial (که در
    ویندوز `out_waiting` را بی‌پایان poll می‌کند) استفاده نمی‌شود؛ خالی شدن بافر خروجی با همان سقف
    منتظر می‌ماند. نوشتن ناقص یا دیرهنگام `AdapterConnectionError` است.
  - **ELM327:** فهرست سفید فقط `AT PC` را اضافه کرده است (Protocol Close، سمت آداپتور، با تأیید صاحب
    پروژه). `_close()` آن را با سقف ۱ ثانیه پیش از بستن درگاه می‌فرستد، فقط اگر دستگاه به `ATZ`
    به‌عنوان ELM327 پاسخ داده باشد؛ شکست آن مانع بسته شدن درگاه نمی‌شود. فهرست سفید OBD با
    `fullmatch` بررسی می‌شود.
  - **CAN واقعی:** فقط `slcan`، `pcan`، `kvaser` (حالت listen-only در درایور python-can دارند)؛ فقط
    ۲۵۰۰۰۰ یا ۵۰۰۰۰۰ bit/s (`check_bitrate`). `UdsLink.open` پیش از اولین ارسال
    `PythonCanBus.probe` را اجرا می‌کند (حذف‌شدنی نیست، `probe_s > 0`): فریم خطا یا وضعیت خطای کنترلر
    (`status_is_ok()` در pcan) یعنی توقف بدون ارسال. باس ساکت بیت‌ریت را تأیید **نمی‌کند** و در لاگ
    ترافیک صریحاً همین نوشته می‌شود. `BusABC.state` در python-can به‌طور پیش‌فرض همیشه `ACTIVE` است و
    `slcan` فریم خطا گزارش نمی‌کند؛ برای همین `UdsClient` اگر درخواستی بی‌پاسخ بماند و
    `IsoTpChannel.frames_received == 0` باشد، `UdsNoTrafficError` (یک `AdapterConnectionError`)
    می‌دهد: جلسه قطع می‌شود و DID بعدی فرستاده نمی‌شود.
  - **ISO-TP/UDS:** شروع مجدد پیام حداکثر `max_restarts` بار و فقط پیش از مهلت `recv`؛ تایمر N_Cr فقط
    با Consecutive Frame معتبر از نو شروع می‌شود؛ سقف کل یک پیام `max_message_s`. P2* اعلام‌شده ECU
    حداکثر `P2_STAR_CEILING_S = 10 s` و هیچ انتظاری از `pending_ceiling_s` فراتر نمی‌رود.
  - **تنظیمات:** `command_timeout_s` در بازه ۰٫۵ تا ۱۰ و `connect_timeout_s` در ۵ تا ۶۰ ثانیه؛ مقدار
    بیرون از بازه رد می‌شود (بی‌صدا تغییر نمی‌کند، مانند ADR ۴۳).
  - **Live Data:** `LiveDataSession.poll` پس از `SILENT_POLL_LIMIT = 3` poll پیاپی بدون هیچ پاسخ
    (همه PIDهای ارسال‌شده بی‌پاسخ یا خراب) `VehicleSilentError` می‌دهد و داشبورد با پیام «سوئیچ بسته
    شد؟» قطع می‌شود. pollی که فقط PID پشتیبانی‌نشده داشت (چیزی ارسال نشد) شمرده نمی‌شود.
  - **`diagnose`:** PID بی‌پاسخ یا خراب در `unanswered_pids` (گزارش نسخه ۳) ثبت می‌شود و بقیه گزارش
    خوانده می‌شود؛ فقط قطع اتصال گزارش را متوقف می‌کند. `--no-live` هیچ PID سرویس ۰۱ نمی‌خواند.
  - روش اولین اتصال به سخت‌افزار و خودرو: [`field-validation-protocol.md`](field-validation-protocol.md).
- **قرارداد فلش Bench (Milestone 7a، فاز ۵، فقط شبیه‌سازی):** `core/bench.py` کار Bench را فقط
  *توصیف* می‌کند و هیچ چیزی ارسال یا روی دیسک نوشته نمی‌کند (تست AST). در این قرارداد:
  - `OperationClass.BENCH_FLASH` فقط با `bench_flash_policy(attestation)` و از یک `BenchAttestation`
    کامل ساخته می‌شود. این attestation تأیید انسانی است: ECU روی میز، منبع تغذیه پایدار با محدودیت
    جریان، سیم‌کشی بررسی‌شده، تأیید صاحب پروژه، نام اپراتور و عبارت دقیق `BENCH FLASH`. سیاست فقط
    برای یک جلسه و در کد ساخته می‌شود؛ تنظیمات و متغیر محیطی هیچ‌وقت به آن نمی‌رسند (تست AST) و
    `DEFAULT_POLICY` آن را ندارد.
  - دو capability وجود دارد: `FlashAuthorization` (مجوز تغییر حافظه برای یک target و یک تصویر مشخص)
    و `BackupReceipt` (رسید پشتیبان تأییدشده؛ قفل «خواندن پیش از نوشتن»). هر دو فقط با یک توکن
    خصوصی ساخته می‌شوند که هر کدام را فقط یک ماژول `tuning` اجازه دارد import کند
    (`flash_gate.py` و `backup_vault.py`، در 7d). هر capability هنگام ساخت خودش را دوباره بررسی
    می‌کند و با `check_for(...)` به target، تصویر، هویت ECU و نوع لینک بسته است.
  - **هر `BenchTargetProfile` غیرشبیه‌سازی‌شده با `BenchTargetNotQualifiedError` (زیرکلاس
    `SafetyGuardError`) رد می‌شود.** تنها target موجود `SIMULATED_BENCH_TARGET` است: یک ناحیه
    ۶۴ KiB در چهار سکتور، به اندازه ROM نمایشی، با مقادیر ساختگی.
  - `SeedKeyProvider` فقط یک interface است. هیچ الگوریتم واقعی در پروژه نیست، هیچ سازوکار
    بارگذاری plugin وجود ندارد و هر کلید فقط یک بار امتحان می‌شود.
  - متدهای `BenchProgrammerPort` که ECU را تغییر می‌دهند پارامتر keyword-only
    `authorization: FlashAuthorization` دارند و erase/download علاوه بر آن `backup: BackupReceipt`
    (تست AST روی امضاها).
  - ماشین حالت ژورنال (`JOURNAL_TRANSITIONS`) یک‌جا تعریف شده است: هیچ مسیری از `pending` بدون
    `backup_verified` به `erasing` نمی‌رسد، و ECU دست‌خورده هیچ‌وقت `aborted` (تمیز) نمی‌شود بلکه
    `interrupted` (dirty) می‌شود.
  - مسیرهای فقط‌خواندنی (`hardware/uds.py`، ELM327، ISO-TP، CAN، شبیه‌ساز فقط‌خواندنی و کل
    `diagnostics`) نه `core.bench` را import می‌کنند و نه `BENCH_FLASH` را می‌شناسند. سطح عمومی
    `UdsClient` و فهرست سفید آن قفل است (تست AST).
  - **ECU برنامه‌پذیر شبیه‌سازی‌شده (Milestone 7b):** `hardware/uds_sim_programmable.py` فقط
    *پاسخ‌دهنده* است. نام سرویس‌های برنامه‌ریزی در آن مجاز است، ولی مانند هر ماژول دیگر هیچ literal
    درخواستی با SID آن سرویس‌ها نمی‌سازد (تست AST). سرور آن فقط با رشته ثابت `"virtual"` باز می‌شود،
    پارامتر `interface` ندارد و فقط `hardware/bench_link.py` (7c) اجازه دارد آن را بسازد یا import کند.
    `SimulatedUdsEcu` فقط‌خواندنی دست‌نخورده است و همچنان هر سرویس نوشتنی را `0x11` جواب می‌دهد.
    همه مقدارها ساختگی‌اند (`SIM-...`) و seed-key یک XOR منتشرشده (`"SIM!"`) است.
  - **برنامه‌ریز Bench (Milestone 7c، فقط شبیه‌ساز):** `hardware/bench_programmer.py` **تنها**
    ماژولی است که درخواستی با SID نوشتنی می‌سازد. چهار گارد AST این را تضمین می‌کنند: یکی literalها
    را در کل پکیج اسکن می‌کند، یکی ثابت‌های `SID_*` با مقدار نوشتنی را فقط در `uds.py` و این ماژول
    مجاز می‌داند، یکی بررسی می‌کند که تنها فراخوانی `send` داخل `_transmit` باشد و اولین دستور آن
    `check_bench_request` باشد، و یکی بررسی می‌کند که اولین دستور هر متد تغییردهنده یک بررسی
    capability باشد. ویژگی‌های این ماژول:
    - **فهرست سفید Bench پیش‌فرض‌-رد است:** `0x10` 01/02/03، `0x11 01`، `0x22` (یک DID)، `0x27` فقط
      سطح target، `0x31 01` فقط روتین‌های erase/check همان target، `0x34`/`0x35` فقط بدون فشرده‌سازی و
      با ALFID `0x44`، `0x36`، `0x37` و `0x3E`. سرویس‌های `0x14`، `0x28`، `0x2E`، `0x2F`، `0x3D`،
      `0x85` و `0x87` با نام رد می‌شوند.
    - **رد آنچه شبیه‌سازی‌شده نیست:** سازنده bus یا target غیرشبیه‌سازی‌شده و target چندناحیه‌ای را رد
      می‌کند، و `unlock` هم provider غیرشبیه‌سازی‌شده یا provider دیگری غیر از provider همان target را.
    - **مجوز و تصویر:** اولین `FlashAuthorization` روی لینک pin می‌شود. داده دانلودشده باید دقیقاً
      SHA-256 تصویر مجاز را داشته باشد.
    - **هویت ECU:** erase و download به هویت خوانده‌شده روی همین لینک نیاز دارند. هویت application باید
      با اثرانگشت پشتیبان یکی باشد. خواندن بعدی در bootloader این پیوند را ضعیف نمی‌کند و فقط
      `hard_reset` آن را پاک می‌کند. هویت فقط-bootloader (بازیابی) پذیرفته می‌شود و پیوند پشتیبان با
      ژورنال کار orchestrator است (7d).
    - **تکرار محدود:** فقط بلوک `0x36` تکرار می‌شود، آن هم پس از timeout یا `0x21`: حداکثر ۲ بار برای هر
      بلوک و ۸ بار در کل انتقال. با `0x70` تا `0x73` و `UdsNoTrafficError` انتقال فوراً قطع می‌شود و
      SecurityAccess فقط یک بار امتحان می‌شود.
    - **لاگ ترافیک:** در سطح درخواست است، نه فریم. برای `0x36` فقط سرآیند ثبت می‌شود و کلید پنهان می‌ماند.
    - **لینک:** `hardware/bench_link.py` تنها سازنده programmer و سرور شبیه‌ساز است و فقط
      `virtual-sim-programmable` با کانال `bench-sim-...` را می‌پذیرد. هر رابط یا کانال دیگری
      `BenchTargetNotQualifiedError` می‌گیرد.
  - **هماهنگ‌کننده فلش (Milestone 7d، فقط شبیه‌ساز):** پنج ماژول `tuning` روی
    `BenchProgrammerPort` کار می‌کنند و `hardware` را import نمی‌کنند (تست AST). سه نویسنده فایل
    در `tuning` وجود دارد: `rom_export`، `backup_vault` و `flash_journal`.
    - **gate:** `authorize_bench_flash` سیاست `BENCH_FLASH`، attestation کامل، شبیه‌سازی‌شده بودن
      attestation و target و لینک، و پوشه پشتیبان قابل‌نوشتن با فضای دو برابر تصویر به‌علاوه ۱ MiB
      را بررسی می‌کند. `authorize_bench_recovery` مجوز را فقط به SHA-256 پشتیبان تأییدشده می‌بندد.
    - **vault:** اگر دو خواندن ECU برابر نباشند هیچ چیزی نوشته نمی‌شود. فایل پشتیبان یک بار با `xb`
      نوشته و fsync می‌شود، مانیفستش کنارش قرار می‌گیرد (`SIMULATED-...`)، فقط‌خواندنی می‌شود و پیش از
      صدور رسید از دیسک بازخوانی می‌شود. `verify` درست پیش از erase دوباره اجرا می‌شود (تست AST روی ترتیب).
    - **ژورنال:** هر مرحله *پیش* از انجامش ثبت می‌شود. نوشتن اتمیک است: `.tmp`، سپس fsync، سپس
      `os.replace`. در ویندوز، رد اشتراک فایل (اسکن آنتی‌ویروس) حداکثر ۲۰ بار، در مجموع حدود یک ثانیه،
      تکرار می‌شود و اگر باز هم رد شود fail-closed است. قفل تک‌نمونه هرگز خودکار شکسته نمی‌شود.
      `JournalInspector` ژورنال dirty یا خراب را مانع هر فلش جدید می‌کند.
    - **D3:** شکست پیش از erase یعنی `10 01` و `aborted` (ECU دست‌نخورده). شکست از erase به بعد یعنی
      `interrupted`، بدون `10 01` و بدون `11 01`. `hard_reset` فقط پس از check routine و read-back
      موفق صدا زده می‌شود (تست AST).
    - **بازیابی:** هیچ پشتیبانی نمی‌گیرد. پشتیبان ثبت‌شده در ژورنال را دوباره بررسی می‌کند، هویت
      bootloader (الگوی bootloader جور و الگوی application ناجور) یا application **همان قطعه** را
      می‌پذیرد، و مراحل ۳ و ۴ را با تصویر پشتیبان اجرا می‌کند. «همان قطعه» یعنی اثرانگشت DIDهای
      `unit_identity_dids` برابر باشد (از 7e.1؛ DIDهای نرم‌افزار ISO 14229 مثل `F189` در آن ممنوع‌اند).
      مسیرهای `recovery_failed` به `recovering` و «پروسه وسط انتقال مُرد» هم پوشش داده شده‌اند.
  - **CLI فلش شبیه‌سازی‌شده (Milestone 7e، تصمیم D7، استثنای کنترل‌شده ADR ۴۶):**
    `app/bench_flash.py` تنها ماژولی است که هم ماژول‌های فلش `tuning` و هم `hardware/bench_link.py`
    را import می‌کند (تست AST). `cli.py` فقط از همین facade استفاده می‌کند.
    - `BenchLinkConfig` همیشه بدون آرگومان ساخته می‌شود، یعنی بدون رابط و بدون کانال (تست AST).
      `bench flash-sim` هیچ گزینه `--interface`، `--channel`، `--port` یا `--adapter` ندارد (تست
      parser). ADR ۴۶ پابرجاست: CLI همچنان ویرایش یا خروجی ROM ندارد.
    - دستورها: `bench flash-sim --output DIR [--rom PATH --maps DEFS] [--fault ...]
      [--allow-unverified-checksum-in-simulation] [--json]`، `bench flash-sim --output DIR --recover`،
      `bench journal show DIR [--json]` و `bench journal discard-stale DIR`. بدون `--rom`، ROM نمایشی
      SIMULATED و تعریف‌هایش استفاده می‌شوند.
    - کد خروج `3` یعنی ژورنال dirty مانده و بازیابی لازم است؛ دستور دقیق `--recover` چاپ می‌شود. کد
      `1` یعنی توقف پیش از erase یا رد شدن توسط ژورنال یا gate. کد `2` یعنی ورودی نادرست، از جمله
      تصویری که flash plan رد می‌کند (مثلاً چک‌سام نامعتبر بدون override شبیه‌سازی).
    - attestation جلسه CLI را سرویس می‌سازد (`simulated=True`، اپراتور «CLI (simulated session)»).
      جملات آن میز *مجازی* را توصیف می‌کنند و gate آن را فقط برای شبیه‌ساز می‌پذیرد.
    - همه چیز در `DIR` می‌ماند: ژورنال و بایگانی آن، پشتیبان‌ها در `backups/` و
      `SIMULATED-bench-ecu-state.json`. این فایل حالت پایدار ECU شبیه‌سازی‌شده است: فلش، رکورد اعتبار
      و شمارنده کلید غلط. پس از هر اجرا به‌صورت اتمیک ذخیره می‌شود، و فایل خراب fail-closed است. هر
      اجرای تازه CLI برای ECU شبیه‌سازی‌شده یک power cycle است، پس `--recover` با همان فلش نیمه‌نوشته
      روبه‌رو می‌شود.
    - faultها: `drop-at-block=N`، `nrc72-at-block=N`، `power-loss-at-block=N` (N از ۱ تا ۶۴؛ بلوک ۱ KiB)،
      `bad-key` و `corrupt-readback`. مورد آخر فقط برای read-back مرحله ۴ مسلح می‌شود؛ اگر از ابتدا
      مسلح شود، دو خواندن پشتیبان را نابرابر می‌کند.
  - **رابط گرافیکی فلش Bench (Milestone 7f، فقط شبیه‌ساز):** `ui/bench_flash_panel.py` فقط Protocol
    `BenchFlashService` در `ui/backend.py` را می‌بیند. ورودی و خروجی آن گزارش‌های `core/bench_reports.py`
    و `BenchAttestation` است و هیچ capability، policy یا نوع `tuning` در `ui` نیست (تست AST).
    - modal تأیید شامل چهار جمله انسانی، بررسی‌های ماشینی ✓/✗ (`preflight_flash`/`preflight_recovery`،
      فقط‌خواندنی)، نام اپراتور و `BENCH FLASH` است. attestation فقط در حافظه همان پنجره است: نه QSettings
      و نه فایل (تست AST). هر `BenchAttestation` ساخته‌شده در `ui` به‌صورت literal `simulated=True` است.
    - کار Bench روی thread جداگانه (`BackgroundRunner(name="bench-flash")`) اجرا می‌شود تا polling زنده
      منتظر نماند. پیشرفت با `ProgressRelay` (سیگنال صف‌شده) به thread رابط کاربری می‌رسد و لغو با
      `CancelFlag` انجام می‌شود.
    - هماهنگ‌کننده درست پس از ثبت `erasing` یک گزارش `ERASE_PROGRESS_MESSAGE` می‌فرستد. سرویس لغو را فقط
      در مراحل `pre_flight` و `backup` (`CANCELLABLE_STAGES`) می‌پذیرد و رابط کاربری از همان گزارش دکمه لغو
      را قفل و هشدار «اتصال را قطع نکنید» را نشان می‌دهد.
    - `MainWindow` هنگام شروع و پس از هر اجرا ژورنال را بررسی می‌کند. برای `dirty` یا `corrupt` بنر قرمز
      بازیابی اضطراری نشان داده می‌شود؛ در حالت `corrupt` دکمه بازیابی غیرفعال است. پوشه داشبورد
      `output_dir/bench-flash-sim` است. شرح کامل در [`bench-flash-protocol.md`](bench-flash-protocol.md).
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

### ۷٫۱ پایگاه دانش خودرو و ECU (Milestone 5)

```
database/data/vehicles/<manufacturer>.json ─► vehicle_schema.parse_pack() ─► core.knowledge (Claim, VehicleProfile)
                                                                              │
             diagnostics.vehicle_matcher.rank_candidates(profiles, ObservedFacts) ◄─ جلسه OBD/UDS (فقط خواندنی)
             diagnostics.evidence_export.build_evidence_bundle(...) ─► evidence/<bundle>.json ─► بازبینی ─► ادعای verified
```

- **هر ادعا یک `Claim`** است: `value`، `confidence` (`verified` > `documented` > `reported` >
  `unverified` > `unknown`)، `source` (شناسه یک `Source` در همان بسته)، `notes`، `verify` (کلید
  راهنمای بررسی میدانی در `guidance.json`) و برای `verified` یک `evidence` (مسیر بسته شواهد + SHA-256).
- **«نامعلوم» صریح است:** `unknown` یعنی `value: null`، بدون منبع، با راهنمای بررسی. فهرست خالی
  (`[]`) یعنی «چیزی ثبت نشده» و در CLI و داشبورد «نامعلوم» نمایش داده می‌شود.
- **ماتریس منبع ↔ اطمینان:** `verified` فقط با `field_evidence`؛ `documented` با نشریه سازنده؛
  `reported` با مرجع عمومی معتبر؛ `unverified` با گزارش انجمن یا فرضیه پروژه. **استاندارد هیچ ادعای
  خاص خودرو را مستند نمی‌کند** (ISO 15765-4 می‌گوید CAN روی OBD چیست، نه اینکه پارس از آن استفاده می‌کند).
- **اعتبارسنج سخت‌گیر** (`database/vehicle_schema.py`، فقط کتابخانه استاندارد، بدون `jsonschema`):
  کلید ناشناخته، مقدار جایگزین («TBD»، «n/a»، «?»، شناسه صفر)، شناسه CAN بیش از ۱۱ بیت، سرعت CAN
  متناقض با پروتکل، الگوی غیر glob، منبع استفاده‌نشده و سرویس غیرخواندنی برای PID اختصاصی را رد می‌کند و
  مسیر JSON خطا را نام می‌برد.
- **بسته‌های محلی:** `knowledge_dir/vehicles/*.json` خودرو یا سازنده هم‌شناسه را جایگزین می‌کند (با لاگ).
  پایگاه دانش در زمان اجرا هیچ فایلی نمی‌نویسد (تست AST).
- **تطبیق غیرفعال:** فقط آنچه جلسه قبلاً خوانده مقایسه می‌شود؛ ادعای `unknown` هیچ امتیازی نمی‌دهد؛
  تطبیق فقط پروتکل حداکثر «ضعیف (فقط سازگار)» است؛ مغایرت با ادعای `verified`/`documented` در WMI یا
  لایه فیزیکی، نامزد را حذف می‌کند. خروجی «سازگار با مشاهدات» است، نه شناسایی.
- **بسته شواهد میدانی:** فقط‌خواندنی، با `content_sha256`؛ داده شبیه‌سازی‌شده با
  `usable_for_verification: false` برچسب می‌خورد. پنهان‌سازی VIN پیش‌فرض است و «بسته به‌صورت
  fail-closed» عمل می‌کند: اگر VIN یا هگز شماره سریالش در خروجی بماند، خروجی ساخته نمی‌شود.
- تست `tests/unit/test_vehicle_database.py` برای هر ادعای `verified` بسته شواهدش را باز می‌کند و بررسی
  می‌کند که هش درست باشد، بسته شبیه‌سازی‌شده نباشد و مقدار ادعا واقعاً مشاهده شده باشد.

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

**افزودن خودرو یا ادعای جدید:** فقط ویرایش JSON در `database/data/vehicles/` (یا بسته محلی در
`knowledge_dir/vehicles/`)؛ بدون تغییر کد پایتون. راهنمای کامل در `docs/development.md` (بخش ۵-ج).

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
| ۳۴ | (Milestone 5) مدل ادعا و سطح اطمینان در `core/knowledge.py` (نه `database`) | `ui` اجازه import از `database` ندارد ولی باید پروفایل‌ها را نمایش دهد؛ مدل‌ها فقط کتابخانه استاندارد‌اند | تعریف مدل‌ها در `database` و تبدیل به dict برای UI |
| ۳۵ | (Milestone 5) اعتبارسنج پایتونی واحد به‌جای JSON Schema | بدون وابستگی جدید (`jsonschema`)، یک منبع حقیقت، پیام خطا با مسیر JSON، قواعد بین‌فیلدی (سرعت در برابر پروتکل، ماتریس منبع) | فایل JSON Schema + اعتبارسنج پایتونی موازی (دو منبع حقیقت) |
| ۳۶ | (Milestone 5) `verified` فقط با بسته شواهد میدانی در `evidence/` که هش، غیرشبیه‌سازی‌بودن و برابری مقدار آن را تست بررسی می‌کند | ادعای «تأییدشده» باید از روی فایل قابل بازتولید باشد | پذیرش `verified` با توضیح متنی |
| ۳۷ | (Milestone 5) الگوهای شماره قطعه/نسخه با glob (`fnmatchcase`)، نه regex | خوانا برای بازبین، بدون خطر ReDoS، کافی برای پیشوندها | regex کامل |
| ۳۸ | (Milestone 5) پنهان‌سازی VIN در بسته شواهد پیش‌فرض و fail-closed؛ پاسخ درخواست‌های VIN (`0902`، `22 F1 90` و فریم‌های ISO-TP آن) کامل جایگزین می‌شود | VIN داده شخصی است؛ هگز آن در فریم‌های چندتایی پخش می‌شود و ماسک متنی کافی نیست | فقط ماسک متن ASCII |
| ۳۹ | (Milestone 5) تطبیق غیرفعال با امتیاز نرمال‌نشده و سقف «ضعیف» برای تطبیق فقط پروتکل | نیمی از خودروها CAN 500k دارند؛ تطبیق پروتکل نباید شبیه شناسایی دیده شود | نرمال‌سازی روی سیگنال‌های موجود |
| ۴۰ | (Milestone 6) مدل‌های مشترک در `core/calibration.py` و Protocol بدون Qt به نام `BenchWorkspace` در `ui/backend.py` که `app/bench.py` پیاده می‌کند | `ui` اجازه import از `tuning` ندارد (مانند ADR ۱۳)؛ CLI و UI یک مسیر مشترک دارند | import مستقیم `tuning` در `ui` |
| ۴۱ | (Milestone 6) تعریف دقیق چک‌سام: `sum16_*` جمع کلمه‌های ۱۶ بیتی؛ CRC-16 از نوع CCITT-FALSE (IBM-3740) | نام‌هایی مثل SUM16 یا CRC16 بدون تعریف دقیق مبهم‌اند؛ مقدار بررسی کاتالوگ CRC تست می‌شود | جمع بایتی ذخیره‌شده در ۱۶ بیت، XMODEM/KERMIT |
| ۴۲ | (Milestone 6) خروجی با چک‌سام نامعتبر رد می‌شود؛ تأیید صریح و مانیفست همیشه لازم است | فایلی که ممکن است با ابزار دیگری فلش شود نباید بی‌صدا ناسازگار یا بی‌سابقه باشد | هشدار و اجازه خروجی |
| ۴۳ | (Milestone 6) هیچ clamp: مقدار خارج از min/max رد می‌شود و خطای کوانتش گزارش می‌شود | clamp خاموش، داده متفاوت از خواست کاربر می‌سازد | محدود کردن خودکار مقدار |
| ۴۴ | (Milestone 6) جدول‌ها هم‌پوشانی ندارند؛ محورها مشترک و فقط‌خواندنی‌اند؛ فیلد چک‌سام روی جدول نمی‌افتد | ویرایش یک جدول نباید جدول دیگری را بی‌صدا تغییر دهد | اجازه هم‌پوشانی با هشدار |
| ۴۵ | (Milestone 6) `simulated` فقط از منبع (سازنده ROM نمایشی)، نه از محتوا؛ فایل روی دیسک شبیه‌سازی‌شده فرض نمی‌شود | مانند ADR ۳۱: برچسب از منبع داده می‌آید | تشخیص از روی رشته سرآیند |
| ۴۶ | (Milestone 6) بدون CLI برای ویرایش/خروجی؛ CLI فقط `inspect`، `table` و `demo` | تغییر ROM باید با تصمیم آگاهانه انسان در رابط کاربری باشد | `bench export` در CLI |
| ۴۷ | (پیش از آزمون میدانی) `write_timeout` در pyserial و انتظار محدود برای خالی شدن بافر به‌جای `flush()` | `flush()` در ویندوز حلقه بی‌پایان دارد؛ آداپتور USB/بلوتوث گیرکرده thread ورودی/خروجی و بستن برنامه را قفل می‌کرد | `flush()` در thread جدا با timeout |
| ۴۸ | (پیش از آزمون میدانی) CAN واقعی: فقط رابط‌های دارای listen-only، فقط ۲۵۰k/۵۰۰k، آزمون listen-only اجباری پیش از اولین ارسال؛ `gs_usb` و `socketcan` حذف شدند | بیت‌ریت اشتباه روی باس خودرو فریم خطا تولید می‌کند و ECUهای دیگر را مختل می‌کند؛ رابطی که نمی‌تواند بی‌ارسال گوش دهد این آزمون را ممکن نمی‌کند | آزمون در حالت عادی (خودش فریم خطا می‌فرستد)، یا نگه داشتن رابط‌ها بدون آزمون |
| ۴۹ | (پیش از آزمون میدانی) `UdsNoTrafficError`: درخواست بی‌پاسخ روی باسی که هیچ فریمی تحویل نداده جلسه را قطع می‌کند | `slcan` فریم خطا و وضعیت باس را گزارش نمی‌کند؛ تنها نشانه باس مختل، سکوت است. ادامه دادن با DIDهای بعدی یعنی ارسال بیشتر روی باس مشکوک | timeout عادی و ادامه با DID بعدی |
| ۵۰ | (پیش از آزمون میدانی) سقف P2* (۱۰ ثانیه)، سقف کل `pending_ceiling_s` برای هر انتظار، سقف شروع مجدد و زمان کل پیام ISO-TP | مقداری که ECU یا گره خراب اعلام/ایجاد می‌کند نباید انتظار را نامحدود کند (P2* تا ۶۵۵ ثانیه قابل اعلام است) | پذیرش P2* سرور بدون سقف |
| ۵۱ | (پیش از آزمون میدانی) بازه مجاز timeoutها در تنظیمات؛ بیرون از بازه رد می‌شود، clamp نمی‌شود | timeout خیلی کوتاه آداپتور را وسط هر درخواست قطع می‌کند (رگبار درخواست روی باس)؛ خیلی بلند آداپتور مرده را پنهان می‌کند. رد صریح مانند ADR ۴۳ اشتباه را نشان می‌دهد | clamp خاموش |
| ۵۲ | (پیش از آزمون میدانی) قطع‌کن مدار در `LiveDataSession` (۳ poll پیاپی بی‌پاسخ) با `VehicleSilentError` | با سوئیچ بسته روی خط K پاسخ‌ها `NO DATA` است و داشبورد تا ابد poll می‌کرد؛ منطق در لایه تشخیص است تا بدون Qt تست شود | شمارنده در `MainWindow` |
| ۵۳ | (پیش از آزمون میدانی، تأیید صاحب پروژه) `AT PC` در فهرست سفید و ارسال آن هنگام قطع؛ `unanswered_pids` و گزارش نسخه ۳؛ `--no-live` | ELM327 تا وقتی از سوکت OBD برق دارد keep-alive خط K را ادامه می‌دهد؛ یک PID بی‌پاسخ نباید گزارش میدانی را از بین ببرد؛ اولین اجرا روی خودروی جدید باید کمترین ترافیک را داشته باشد | فقط بستن درگاه؛ شکست کل گزارش |
| ۵۴ | (Milestone 7a، تأیید صاحب پروژه) Milestone 7 فقط با ECU شبیه‌سازی‌شده؛ `BenchTargetProfile` واقعی در زمان ساخت رد می‌شود | هیچ شاهدی از چیدمان حافظه، روتین‌ها یا الگوریتم seed-key هیچ ECU واقعی نداریم؛ مسیر واقعی بدون شاهد قابل تست نیست و خطرناک است | پرچم پیکربندی برای ECU واقعی |
| ۵۵ | (Milestone 7a) کلاس عملیات جدید `BENCH_FLASH` به‌جای `CALIBRATION_WRITE` به‌همراه `bench_mode` | مرز روشن و قابل‌کنترل با AST؛ فقط `bench_flash_policy` از روی attestation کامل آن را می‌دهد | استفاده دوباره از `CALIBRATION_WRITE` |
| ۵۶ | (Milestone 7a) capabilityهای `FlashAuthorization` و `BackupReceipt` با توکن خصوصی، اعتبارسنجی دوباره در زمان ساخت و استفاده، و فقط یک ماژول سازنده برای هر کدام | پایتون سازنده خصوصی ندارد؛ ترکیب توکن، تست AST و بررسی در زمان اجرا، دور زدن را به یک تصمیم آشکار تبدیل می‌کند | پرچم بولی `backup_done` |
| ۵۷ | (Milestone 7a) قراردادها (`BenchProgrammerPort`، `SeedKeyProvider`) در `core` | `tuning` فقط `core` را import می‌کند و `hardware` باید port را پیاده کند؛ وارونه‌سازی وابستگی بدون شکستن قواعد لایه‌ها | import `hardware` در `tuning` |
| ۵۸ | (Milestone 7a) `BenchAttestation` (تأیید انسانی) جدا از بررسی‌های ماشینی؛ `BenchSafetyChecklist` برای سازگاری می‌ماند | نرم‌افزار ولتاژ منبع یا میز کار را نمی‌بیند؛ پشتیبان و اعتبار ROM را خودش ثابت می‌کند، پس نباید آن‌ها را از کاربر بپرسد | یک چک‌لیست مخلوط |
| ۵۹ | (Milestone 7a) `SeedKeyProvider` فقط interface؛ بدون الگوریتم واقعی و بدون سازوکار plugin | ساختن یا مهندسی معکوس الگوریتم سازنده ممنوع است (CLAUDE.md بخش ۶)؛ بارگذاری کد خارجی مسیر تزریق است | ورود provider از entry point |
| ۶۰ | (Milestone 7b) ECU برنامه‌پذیر شبیه‌سازی‌شده: اعتبار برنامه فقط با checkMemory موفق روی کل فلش و تا وقتی CRC همان است؛ حالت پایدار (فلش، رکورد اعتبار، شمارنده کلید غلط) از RAM جداست؛ هر fault فقط یک بار رخ می‌دهد | همان رفتاری که bootloader واقعی دارد، بدون وابستگی به محتوای تصویر؛ قطع برق باید فلش نیمه‌نوشته و bootloader را باقی بگذارد؛ fault یک‌باره اجازه می‌دهد بازیابی بعدی به‌طور قطعی تست شود | CRC درون خود تصویر؛ fault دائمی |
| ۶۱ | (Milestone 7c) `bench_programmer.py` تنها سازنده درخواست‌های نوشتنی، با فهرست سفید Bench پیش‌فرض‌-رد خودش و حلقه UDS مستقل؛ `uds.py` و ELM327 دست‌نخورده و قفل (تست AST) | یک نقطه واحد و قابل‌بررسی برای هر درخواست تغییردهنده؛ مسیر فقط‌خواندنی هیچ ریسکی از کار Bench نمی‌گیرد (تصمیم D5) | refactor به transport مشترک با `uds.py` |
| ۶۲ | (Milestone 7c) گارد AST برای ثابت‌های `SID_*` با مقدار نوشتنی | اسکن literal فقط عدد را می‌دید و `bytes([SID_X, ...])` را نمی‌گرفت؛ این شکاف در 7c پیدا و بسته شد | فقط اسکن literal |
| ۶۳ | (Milestone 7c) پیوند هویت: هویت application روی لینک با خواندن در bootloader ضعیف نمی‌شود و فقط reset آن را پاک می‌کند | تست نشان داد که بدون این قاعده، خواندن دوباره هویت در جلسه programming پشتیبان ECU دیگری را می‌پذیرفت | آخرین خواندن جایگزین قبلی |
| ۶۴ | (Milestone 7c) فقط `0x36` تکرار می‌شود (timeout یا `0x21`، ۲ بار برای هر بلوک و ۸ بار برای هر انتقال)؛ SecurityAccess هرگز | تکرار بلوک با همان شمارنده ایمن است (ECU تکرار را تأیید می‌کند)؛ تکرار کلید شمارنده تلاش ECU را مصرف می‌کند و می‌تواند قفل ایجاد کند | تکرار عمومی همه درخواست‌ها |
| ۶۵ | (Milestone 7d) هماهنگ‌کننده فقط روی `BenchProgrammerPort` و در `tuning`؛ اتصال واقعی در `app` | قاعده لایه‌ها (`tuning` فقط `core`) و تست‌پذیری قطعی با باس جعلی؛ همان کد روی باس virtual و (در آینده) روی هر port دیگر | import `hardware` در `tuning` |
| ۶۶ | (Milestone 7d) پشتیبان: دو خواندن مستقل و برابر، نوشتن یک‌باره `xb`، فقط‌خواندنی، بازخوانی از دیسک، و `verify` دوباره درست پیش از erase | خواندن ناپایدار نباید تنها نسخه شود؛ فایلی که پس از صدور رسید عوض شود (TOCTOU) نباید مبنای بازیابی باشد | یک خواندن و اعتماد به حافظه |
| ۶۷ | (Milestone 7d) ژورنال پیش از هر مرحله و اتمیک؛ قفل O_EXCL که خودکار شکسته نمی‌شود؛ ژورنال خراب = fail-closed | پس از crash ژورنال هرگز کمتر از آنچه ممکن است رخ داده باشد ادعا نمی‌کند؛ تشخیص زنده بودن PID در ویندوز با `os.kill` خطرناک است | شکستن خودکار قفل کهنه |
| ۶۸ | (Milestone 7d) تکرار محدود `os.replace`/unlink در برابر `PermissionError` (۲۰ بار، ۵۰ ms) | در اجرای تست‌ها روی ویندوز دیده شد: اسکن آنتی‌ویروس فایل تازه را لحظه‌ای باز نگه می‌دارد و rename رد می‌شود؛ همین روی لپ‌تاپ میدانی وسط فلش رخ می‌دهد | شکست فوری (فلش بی‌دلیل interrupted می‌شد) |
| ۶۹ | (Milestone 7d) گارد نویسنده فایل در `tuning` حالت `Path.open(mode)` را هم می‌بیند | اسکن قبلی فقط آرگومان دوم `open(path, mode)` را می‌خواند و `Path.open("wb")` از آن رد می‌شد | فقط `open` داخلی |
| ۷۰ | (Milestone 7e، تصمیم D7) `bench flash-sim` و `bench journal` در CLI فقط برای ECU شبیه‌سازی‌شده؛ استثنای کنترل‌شده ADR ۴۶ | بازیابی و تزریق خطا باید بدون GUI و به‌طور قطعی قابل تکرار و تست باشد؛ نبودِ هر گزینه رابط/کانال/درگاه و ساخت `BenchLinkConfig()` بدون آرگومان با تست AST و parser قفل است | فلش فقط از GUI؛ گزینه `--interface` با رد در زمان اجرا |
| ۷۱ | (Milestone 7e) `app/bench_flash.py` تنها نقطه اتصال `tuning` و `hardware` برای فلش Bench؛ خروجی آن گزارش‌های ساده `core/bench_reports.py` است، نه `RecoveryAssessment` | `ui` اجازه import از `tuning` ندارد (ADR ۱۳ و ۴۰)؛ CLI و داشبورد 7f یک facade و یک مدل مشترک دارند | بازگرداندن مستقیم انواع `tuning` به UI |
| ۷۲ | (Milestone 7e) حالت پایدار ECU شبیه‌سازی‌شده (`SimulatedEcuState`) در پوشه Bench ذخیره و هر اجرای CLI یک power cycle است | بدون آن، `--recover` در فرایند جدید با ECU کارخانه‌ای روبه‌رو می‌شد و بازیابی فلش نیمه‌نوشته را واقعاً آزمایش نمی‌کرد | ECU تازه در هر اجرا |
| ۷۳ | (Milestone 7e) `corrupt-readback` فقط هنگام شروع مرحله ۴ مسلح می‌شود | `FaultPlan` بلوک‌ها را در هر انتقال می‌شمارد؛ مسلح از ابتدا، اولین خواندن پشتیبان را خراب و فلش را پیش از erase متوقف می‌کرد | شمارش سراسری بلوک‌ها در شبیه‌ساز |
| ۷۴ | (Milestone 7e) اسکن import در تست معماری `from pkg import submodule` را به‌صورت `pkg.submodule` ثبت می‌کند و import نسبی در `src` ممنوع است | تست جهش نشان داد `from iran_obd_tuner.hardware import bench_link` فقط `iran_obd_tuner.hardware` ثبت می‌شد و import نسبی اصلاً دیده نمی‌شد؛ پس هر قاعده «فقط X اجازه import از Y دارد» دورزدنی بود | فقط `node.module` |
| ۷۵ | (Milestone 7e.1، تأیید صاحب پروژه: گزینه A) `BackupReceipt` به `unit_fingerprint` بسته است: SHA-256 روی `BenchTargetProfile.unit_identity_dids` (شماره سخت‌افزار و سریال)، نه روی همه DIDها؛ DIDهای شناسایی نرم‌افزار رد می‌شوند؛ مانیفست نسخه ۲ | اثرانگشت کامل `F189` را دارد و هر فلش آن را عوض می‌کند؛ پس از fault در `verifying` بازیابی برای همیشه رد می‌شد. هدف پیوند (پشتیبان ECU الف روی ECU ب نرود) با سریال برآورده می‌شود؛ اثرانگشت کامل فقط برای ثبت می‌ماند (تست AST) | بازبینی دوباره مرحله ۴ به‌جای بازگرداندن؛ دستور `abandon` |
| ۷۶ | (Milestone 7e.1) «در bootloader» یعنی الگوی bootloader جور **و** الگوی application ناجور (`shows_bootloader`) | الگوی bootloader (`F191`) با هویت application هم جور است و بررسی قبلی هر ECU آن خانواده را bootloader حساب می‌کرد | فقط الگوی bootloader |
| ۷۷ | (Milestone 7f) Protocol به نام `BenchFlashService` در `ui/backend.py` با گزارش‌های `core/bench_reports.py` (`BenchPreflightReport`، `BenchCheck`)؛ `app/bench_flash.py` آن را پیاده می‌کند (تست تطابق امضاها) | همان الگوی ADR ۴۰: `ui` به `tuning` و capabilityها دسترسی ندارد و CLI و داشبورد یک facade مشترک دارند | import مستقیم `tuning` یا ساخت مجوز در `ui` |
| ۷۸ | (Milestone 7f، تصمیم D4) attestation داشبورد فقط در حافظه پنجره و فقط `simulated=True`؛ هر اجرا دوباره از gate می‌گذرد | تأیید انسانی برای یک جلسه است؛ ذخیره آن یعنی حالت فلش از تنظیمات زنده شود (ADR ۵) | QSettings یا فایل تنظیمات |
| ۷۹ | (Milestone 7f) نقطه بی‌بازگشت به‌صورت گزارش پیشرفت `erase` (`core.bench.ERASE_PROGRESS_MESSAGE`) پس از ثبت `erasing`؛ لغو فقط در `pre_flight`/`backup` | رابط کاربری باید دقیقاً همان لحظه قفل شود؛ حدس زدن از اولین بلوک انتقال یک فاصله کور می‌گذاشت که در آن لغو بی‌اثر بود (تصمیم D3) | لغو در هر مرحله؛ بدون گزارش erase |
| ۸۰ | (Milestone 7f) thread جداگانه برای کار Bench و رله پیشرفت با سیگنال صف‌شده Qt؛ قطعه‌های چپ‌به‌راست (مسیر، هش، جزئیات فنی) در متن فارسی با LRI/PDI ایزوله می‌شوند | فلش چند ثانیه طول می‌کشد و نباید polling زنده را متوقف کند؛ رندر واقعی نشان داد مسیر ویندوز و «N free, M needed» در متن راست‌به‌چپ جابه‌جا می‌شوند | همان thread آداپتور؛ نمایش خام |
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
    این یعنی باز شدن مجدد درگاه در هر کلیک (و یک ثانیه آزمون listen-only در هر بار). نگه داشتن لینک و
    TesterPresent دوره‌ای پیاده نشده است.
19. **آزمون listen-only بیت‌ریت را فقط وقتی تأیید می‌کند که باس ترافیک داشته باشد.** پشت gateway باس OBD
    ممکن است ساکت باشد؛ آن‌وقت فقط قاعده ADR ۴۹ از ارسال بیشتر جلوگیری می‌کند. پروتکل میدانی استفاده
    از رابط CAN واقعی را تا اعتبارسنجی جداگانه ممنوع کرده است.
20. **`AT PC` و رفتار حافظه پروتکل کلون‌ها** روی سخت‌افزار واقعی بررسی نشده‌اند؛ اینکه جستجوی خودکار
    `AT TP 0` در کلون‌ها پروتکل را در حافظه ذخیره می‌کند یا نه، باید روی میز کار دیده شود.
21. ~~**بازیابی پس از fault در مرحله `verifying`**~~ — **برطرف شد (Milestone 7e.1، ADR ۷۵ و ۷۶).**
    پشتیبان حالا به DIDهای هویت قطعه بسته است (`F191` و `F18C` در target شبیه‌سازی‌شده)، نه به نسخه
    نرم‌افزار. بازیابی روی همان قطعه، حتی وقتی تصویر جدید را اجرا می‌کند، پشتیبان را برمی‌گرداند. باقی‌مانده:
    ECU در bootloader سریال گزارش نمی‌دهد، پس پیوند bootloader همچنان فقط بر الگوی `F191` تکیه دارد؛
    برای ECU واقعی، profile باید نشان دهد کدام DID در bootloader قطعه را ثابت می‌کند.

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

- هر نوع نوشتن/فلش/Security Access
- خواندن ROM از ECU، فلش و بازگردانی (فاز ۵، قفل)؛ پارس A2L/DAMOS، Intel HEX/S-record و طرح‌های
  چک‌سام چندبخشی/اختصاصی
- تعریف نقشه یا آفست واقعی برای هر ECU خودروهای ایرانی (بدون شواهد مستند)
- ارتقای خودکار ادعاها از روی بسته شواهد (ارتقا یک ویرایش بازبینی‌شده JSON است)
- ضبط بلندمدت Live Data، چند-خودرویی، شبکه/همگام‌سازی ابری
