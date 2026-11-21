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
| ورودی خط فرمان با دستورات `version`, `adapters`, `config`, `diagnose`, `uds-ident`, `vehicles` | `src/iran_obd_tuner/app/cli.py` |
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
| **لایه CAN و ISO-TP (ISO 15765-2) خودِ پروژه** روی python-can (اختیاری، `[can]`): رابط‌های `slcan`، `pcan`، `kvaser` و باس مجازی؛ Single/First/Consecutive Frame، Flow Control (BS، STmin، Wait)، padding، timeoutهای N_Bs/N_Cr، سقف شروع مجدد و سقف زمان کل پیام. نام رابط همیشه صریح است؛ رابط واقعی فقط با ۲۵۰ یا ۵۰۰ kbit/s و پس از **آزمون فقط‌شنود (listen-only)** باز می‌شود — **روی رابط CAN واقعی اعتبارسنجی نشده** | `src/iran_obd_tuner/hardware/can_bus.py`, `isotp.py` |
| **کلاینت UDS (ISO 14229-1) فقط‌خواندنی:** `0x10` فقط ۰۱/۰۳، `0x3E`، `0x22` و `0x19` (۰۱/۰۲)، با فهرست سفید سخت‌کد که سرویس‌های نوشتن/امنیت/برنامه‌ریزی (`0x11`، `0x14`، `0x27`، `0x2E`، `0x2F`، `0x31`، `0x34` تا `0x37` و چند سرویس دیگر) و جلسه برنامه‌ریزی `0x02` را همیشه رد می‌کند؛ مدیریت NRC (`0x78` با سقف، `0x21`، `0x31`، `0x33`، `0x22`) | `src/iran_obd_tuner/hardware/uds.py` |
| شناسایی ECU با UDS: DIDهای استاندارد `F190` (VIN)، `F187`، `F189`، `F191`، `F197` و کدهای خطای UDS، هر کدام با وضعیت مستقل؛ جلسه Extended فقط با انتخاب صریح کاربر و همیشه با بازگشت به `0x10 0x01`؛ جستجوی ECU با `3E 00` روی `0x7DF` فقط با کلیک کاربر | `src/iran_obd_tuner/diagnostics/ecu_identification.py`, `ui/uds_view.py` |
| ECU شبیه‌سازی‌شده قطعی روی باس مجازی python-can (`virtual-sim`)، با برچسب «شبیه‌سازی» در CLI، JSON، رابط کاربری و ترافیک | `src/iran_obd_tuner/hardware/uds_sim.py`, `uds_link.py` |
| گزارش JSON نسخه ۳ (`schema_version: 3`) با `monitor_status`، `dtc_categories`، `freeze_frame` و `unanswered_pids` (PIDهایی که پاسخ ندادند جدا از PIDهای پشتیبانی‌نشده) | `src/iran_obd_tuner/diagnostics/models.py` |
| خواندن داده در thread پس‌زمینه (رابط کاربری هنگام جستجوی پروتکل قفل نمی‌شود) | `src/iran_obd_tuner/ui/worker.py` |
| دیتابیس دانش JSON (زیرمجموعه عمومی کدهای خطا) | `src/iran_obd_tuner/database/` |
| **پایگاه دانش خودروها و ECUهای بازار ایران (Milestone 5):** هر ادعا سطح اطمینان (`verified` / `documented` / `reported` / `unverified` / `unknown`) و منبع قابل ردیابی دارد؛ بسته‌های JSON برای ایران‌خودرو (پژو پارس TU5/XU7، پژو ۲۰۶ تیپ ۲/۵، سمند و دنا EF7)، سایپا (پراید، تیبا، ساینا، کوییک) و ام‌وی‌ام (۳۱۵، X22) با اعتبارسنج سخت‌گیر. **همه پروتکل‌ها، شناسه‌های CAN و خانواده‌های ECU «نامعلوم»‌اند** تا شواهد میدانی برسد | `core/knowledge.py`, `database/vehicle_schema.py`, `database/vehicles.py`, `database/data/vehicles/` |
| تطبیق غیرفعال خودرو/ECU با داده‌های یک جلسه (پروتکل، PIDها، VIN، DIDهای UDS)، بدون ارسال هیچ درخواست؛ نتیجه «سازگار با مشاهدات» است، نه شناسایی قطعی | `diagnostics/vehicle_matcher.py` |
| خروجی **بسته شواهد میدانی** (Field Evidence Bundle): پروتکل، PIDها، DIDهای شناسایی و ترافیک خام در یک JSON با هش SHA-256؛ شش نویسه آخر VIN به‌طور پیش‌فرض پنهان می‌شود و اگر VIN جایی بماند خروجی رد می‌شود | `diagnostics/evidence_export.py`, `evidence/` |
| تب «پایگاه دانش خودرو» در داشبورد (دوزبانه، نشان‌های رنگی + متنی اطمینان، تطبیق و خروجی شواهد) | `ui/knowledge_view.py` |
| **تحلیل آفلاین فایل ROM (Milestone 6، فاز ۴):** بارگذاری فقط‌خواندنی `.bin` (۱۶ کیلوبایت تا ۸ مگابایت)، SHA-256 و CRC32، رشته‌های ASCII مشاهده‌شده (بدون ادعای شناسایی)؛ فایل اصلی تغییرناپذیر است | `tuning/rom_loader.py` |
| فایل تعریف نقشه (JSON) با منبع و سطح اطمینان، بررسی سازگاری با ROM، جدول‌های ۱D/۲D، ویرایش محدود (بدون clamp) با Undo/Redo و تفاوت اصلی/اصلاح‌شده | `tuning/map_definitions.py`, `tables.py`, `workspace.py` |
| موتور چک‌سام با تعریف دقیق: `sum8`، `sum16_le/be`، `crc16_ccitt_false`، `crc32`، `twos_complement_8/16_le`؛ «معتبر» یعنی مطابق الگوریتم تعریف‌شده، نه پذیرش ECU | `tuning/checksum.py` |
| خروجی ROM اصلاح‌شده **فقط به فایلی دیگر** (هرگز روی فایل اصلی)، با تأیید صریح کاربر، رد شدن در صورت چک‌سام نامعتبر، و فایل مانیفست (`validated_for_vehicle: false`). **هیچ فلشی وجود ندارد** | `tuning/rom_export.py` |
| تب «میز کار ROM» در داشبورد (نقشه حرارتی، نماهای اصلی/اصلاح‌شده/تفاوت/٪، Undo/Redo، وضعیت چک‌سام) و CLI `bench inspect/table/demo` | `ui/bench_view.py`, `app/bench.py`, `app/cli.py` |
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
- ECU شبیه‌سازی‌شده UDS (`can_interface = "virtual-sim"`، پیش‌فرض) مقادیر آشکارا ساختگی برمی‌گرداند
  (`SIMULATEDVIN00004`، `SIM-SW-1.0`، ...). هیچ نام ECU، شماره قطعه یا شناسه CAN واقعی خودروهای
  ایرانی تقلید نشده است.
- ROM نمایشی میز کار (`tuning/demo.py`، ۶۴ کیلوبایت) در کد ساخته می‌شود و سرآیندش می‌گوید
  `SIMULATED-DEMO-ROM / NOT AN ECU IMAGE`؛ تعریف نقشه‌هایش (`tuning/data/demo_maps.json`) هیچ آفست
  واقعی هیچ ECU را توصیف نمی‌کند. پروژه هیچ تعریف نقشه واقعی برای خودروهای ایرانی ندارد.

### برنامه‌ریزی‌شده (پیاده‌سازی نشده — بدون ادعای پشتیبانی)

| قابلیت | فاز | پیش‌نیاز |
|---|---|---|
| اعتبارسنجی آداپتور ELM327 روی سخت‌افزار و خودروی واقعی (پژو پارس TU5) | ۱ | آداپتور فیزیکی + خودرو |
| تفکیک چند ECU با headerها، freeze frameهای غیر از ۰۰، PID `0141` | ۱ | — |
| تأیید جدول بیت‌های PID `0101` با متن SAE J1979 | ۱ | دسترسی به استاندارد |
| مقایسه زمان‌سنج و دینوی مجازی با GPS و دینامومتر واقعی | ۱ | سخت‌افزار و خودرو |
| اعتبارسنجی CAN/ISO-TP/UDS روی رابط و ECU واقعی؛ تأیید اینکه پژو پارس TU5 روی پایه‌های CAN سوکت OBD اصلاً UDS پاسخ می‌دهد | ۲ | رابط CAN + خودرو |
| خواندن DIDهای غیراستاندارد و شناسه‌های CAN ویژه خودروهای ایرانی | ۳ | منبع مستند، نه حدس |
| پر کردن ادعاهای «نامعلوم» پایگاه دانش (پروتکل درگاه OBD، شناسه‌های CAN، خانواده ECU) و ارتقای آن‌ها به `verified` | ۳ | بسته شواهد میدانی از خودروی واقعی |
| تعریف نقشه و چک‌سام واقعی برای یک خانواده ECU ایرانی (طرح‌های چندبخشی/اختصاصی چک‌سام، Intel HEX/S-record) | ۴ | شواهد مستند، نه حدس |
| خواندن ROM از ECU روی Bench، فلش و مکانیزم بازگردانی | ۵ | **قفل** — تأیید صریح، مسیر کنترل‌شده و ECU آزمایشگاهی |

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
  می‌شوند. برای جستجوی پروتکل از `AT TP 0` استفاده می‌شود که چیزی در آداپتور ذخیره نمی‌کند. هنگام قطع
  اتصال `AT PC` (Protocol Close، فقط سمت آداپتور) فرستاده می‌شود تا پیام‌های keep-alive خط K به ECU قطع
  شود؛ این دستور فقط به دستگاهی فرستاده می‌شود که خود را ELM327 معرفی کرده است.
- **همه انتظارها محدودند (پیش از آزمون میدانی سخت‌گیرانه‌تر شد):** نوشتن روی درگاه سریال حداکثر ۱ ثانیه
  (`write_timeout`، بدون `flush()` نامحدود pyserial در ویندوز)؛ `command_timeout_s` فقط ۰٫۵ تا ۱۰ و
  `connect_timeout_s` فقط ۵ تا ۶۰ ثانیه (مقدار بیرون از بازه رد می‌شود)؛ اگر داشبورد سه بار پیاپی هیچ
  پاسخی از خودرو نگیرد (مثلاً سوئیچ بسته شد) ارسال را متوقف و اتصال را قطع می‌کند؛ در `diagnose` بی‌پاسخ
  ماندن یک PID کل گزارش را خراب نمی‌کند. روش آزمون میدانی:
  [`docs/field-validation-protocol.md`](docs/field-validation-protocol.md).
- درگاه سریال **هرگز خودکار جستجو نمی‌شود**؛ کاربر باید آن را صریحاً انتخاب کند تا فرمانی به دستگاه ناشناخته
  ارسال نشود. اگر دستگاه روی درگاه به `ATZ` به‌عنوان ELM327 پاسخ ندهد، هیچ فرمان دیگری ارسال نمی‌شود.
- انتخاب «آداپتور واقعی» در داشبورد **هرگز** بی‌صدا به شبیه‌ساز تبدیل نمی‌شود؛ اگر اتصال شکست بخورد،
  خطا نمایش داده می‌شود.
- **UDS روی CAN:** هر درخواست از یک نقطه واحد (`hardware/uds.check_request_allowed`) با فهرست سفید
  پیش‌فرض‌-رد عبور می‌کند. سرویس‌های `0x11` ECUReset، `0x14` ClearDiagnosticInformation، `0x27`
  SecurityAccess، `0x2E` WriteDataByIdentifier، `0x2F` IOControl، `0x31` RoutineControl، `0x34` تا
  `0x37` (دانلود/آپلود/انتقال) و جلسه برنامه‌ریزی `0x02` و جلسه‌های سازنده **همیشه** رد می‌شوند. جلسه
  Extended (`0x10 0x03`) پیش‌فرض خاموش است، در تنظیمات و متغیر محیطی **وجود ندارد**، فقط با چک‌باکس
  رابط کاربری یا `--extended-session` برای همان اجرا فعال می‌شود و ECU همیشه به `0x10 0x01` برمی‌گردد.
  تست‌های AST وجود نداشتن درخواست نوشتنی را در کد بررسی می‌کنند. هنگام کار با رابط واقعی: سوئیچ باز،
  موتور خاموش، خودرو متوقف. رابط CAN واقعی فقط `slcan`، `pcan` یا `kvaser` است (درایور python-can برای
  `gs_usb` و `socketcan` حالت listen-only ندارد)، فقط با ۲۵۰۰۰۰ یا ۵۰۰۰۰۰ bit/s، و پیش از اولین ارسال
  یک ثانیه فقط گوش می‌دهد: فریم خطا یا وضعیت خطای کنترلر یعنی توقف بدون هیچ ارسالی. اگر اولین درخواست
  بی‌پاسخ بماند و باس هیچ فریمی تحویل نداده باشد، جلسه فوراً و بدون تکرار قطع می‌شود؛ P2* اعلام‌شده
  ECU حداکثر ۱۰ ثانیه پذیرفته می‌شود.
- پیش‌نیازهای هر نوشتن آینده در `tuning/interfaces.py` (`BenchSafetyChecklist`) فهرست شده‌اند.
- **میز کار ROM (فاز ۴) فقط روی فایل کار می‌کند:** فایل اصلی فقط خوانده می‌شود و در حافظه تغییرناپذیر
  است؛ خروجی اصلاح‌شده فقط به مسیر دیگری نوشته می‌شود (با تأیید صریح، چک‌سام معتبر و مانیفست). این
  فایل برای هیچ ECU تأیید نشده و برنامه هیچ راهی برای فلش کردن آن ندارد (فاز ۵ قفل است).

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
iran-obd-tuner diagnose --adapter elm327 --port COM3 --no-live   # بدون Live Data: کمترین ترافیک (گام اول روی خودروی جدید)
iran-obd-tuner uds-ident --discover                    # شناسایی UDS با ECU شبیه‌سازی‌شده (نیازمند [can])
iran-obd-tuner uds-ident --json --output output/ecu.json
iran-obd-tuner uds-ident --interface slcan --channel COM5 --bitrate 500000   # رابط واقعی، فقط‌خواندنی
iran-obd-tuner vehicles list                           # پایگاه دانش خودرو: همه خودروها با سطح اطمینان
iran-obd-tuner vehicles list --manufacturer ikco --json
iran-obd-tuner vehicles show ikco.peugeot_pars         # همه ادعاها، منبع‌ها و روش بررسی میدانی
iran-obd-tuner vehicles match --report output/report.json --uds output/ecu.json   # تطبیق آفلاین
iran-obd-tuner bench demo --output output/demo          # ROM نمایشی شبیه‌سازی‌شده + تعریف نقشه‌ها
iran-obd-tuner bench inspect output/demo/simulated_demo_rom.bin --maps output/demo/simulated_demo_maps.json
iran-obd-tuner bench table dump.bin --maps defs.json --map demo_fuel_base --json   # فقط‌خواندنی

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

CI (`.github/workflows/ci.yml`) روی پایتون ۳٫۱۱، ۳٫۱۲ و ۳٫۱۳ همین سه گیت را با extra `[can]`
اجرا می‌کند تا تست‌های ISO-TP/UDS روی باس مجازی python-can (بدون هیچ رابط فیزیکی) اجرا شوند. یک
job دیگر بدون extras اجرا می‌شود تا نبودِ وابستگی‌های اختیاری هم تست شود. `[gui]` در CI نصب نمی‌شود.

## ساختار پروژه

```
iran-obd-tuner/
├─ src/iran_obd_tuner/               کد برنامه (بسته واقعی، src-layout)
│  ├─ app/                           ورودی‌ها و سیم‌کشی: cli.py، gui_main.py، application.py، dashboard.py
│  ├─ core/                          تنظیمات، لاگ، مدل‌ها، کاتالوگ PID/DTC، مرز ایمنی
│  ├─ hardware/                      قرارداد آداپتور (base.py)، شبیه‌ساز (mock.py)، ELM327، رجیستری، فهرست درگاه‌ها،
│  │                                 CAN (can_bus.py)، ISO-TP (isotp.py)، UDS (uds.py، uds_link.py، uds_sim.py)
│  ├─ diagnostics/                   گزارش یک‌باره (service.py)، نشست داده زنده (live_data.py)، ضبط CSV (recorder.py)،
│  │                                 شناسایی ECU با UDS (ecu_identification.py)، تطبیق خودرو (vehicle_matcher.py)،
│  │                                 بسته شواهد میدانی (evidence_export.py)
│  ├─ database/                      دیتابیس دانش (JSON) + داده‌های همراه بسته، بسته‌های خودرو (data/vehicles/)
│  │                                 و اعتبارسنج آن‌ها (vehicle_schema.py، vehicles.py)
│  ├─ tuning/                        تحلیل آفلاین ROM (rom_loader، map_definitions، tables، workspace، checksum،
│  │                                 rom_export، demo) + مرز نوشتن روی ECU (interfaces.py، بدون پیاده‌سازی)
│  └─ ui/                            داشبورد PySide6: backend.py، gauges.py، i18n.py، icon.py، safety_modal.py، main_window.py،
│                                    diagnostics_view.py، performance_view.py، traffic_view.py، uds_view.py، knowledge_view.py
├─ tests/                            unit/ ، integration/ (CLI + GUI)، fixtures/ (شامل بسته خودروی ساختگی آزمایشی)
├─ evidence/                         بسته‌های شواهد میدانی بازبینی‌شده که ادعاهای verified به آن‌ها ارجاع می‌دهند
├─ config/example.toml               نمونه پیکربندی (کپی به config/local.toml)
├─ docs/                             idea-report.md، architecture.md، development.md، field-validation-protocol.md
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
- [`docs/field-validation-protocol.md`](docs/field-validation-protocol.md) — روش ایمن اولین اتصال به آداپتور و خودروی واقعی
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — شاخه، کامیت، Pull Request، بازبینی
- [`docs/idea-report.md`](docs/idea-report.md) — سند مفهومی و نقشه راه محصول
- [`docs/ai-assistant-rules.md`](docs/ai-assistant-rules.md) — قواعد پروژه برای دستیارهای هوش مصنوعی

## نقشه راه

- **فاز ۰ (انجام‌شده):** ساختار پروژه، مرز ایمنی، جریان تشخیص شبیه‌سازی‌شده، تست و CI
- **فاز ۱ (نیمه‌انجام‌شده):** داشبورد PySide6 با داده شبیه‌سازی‌شده کار می‌کند. Milestone 1
  انجام شده است: داشبورد و CLI حالا یک مسیر داده واحد (`ObdAdapter`) دارند، داده ساختگی و
  مسیر پاک کردن کدها حذف شده و داده شبیه‌سازی همه‌جا برچسب دارد. Milestone 2 هم انجام شده است:
  آداپتور ELM327 فقط‌خواندنی و خواندن در پس‌زمینه. اعتبارسنجی روی آداپتور و خودروی واقعی باقی مانده است.
- **فاز ۲ (پایه انجام‌شده):** Milestone 4: لایه CAN، ISO-TP و کلاینت UDS فقط‌خواندنی، شناسایی ECU
  و VIN با DID `F190`، ECU شبیه‌سازی‌شده روی باس مجازی. اعتبارسنجی روی رابط CAN و خودروی واقعی باقی مانده است.
- **فاز ۳ (ساختار انجام‌شده):** Milestone 5: پایگاه دانش خودروها و ECUهای ایرانی با سطح اطمینان و منبع
  برای هر ادعا، تطبیق غیرفعال، بسته شواهد میدانی، CLI `vehicles` و تب داشبورد. محتوای فنی (پروتکل، شناسه
  CAN، خانواده ECU) تا رسیدن شواهد از خودروی واقعی «نامعلوم» است.
- **فاز ۴ (تحلیل آفلاین انجام‌شده):** Milestone 6: بارگذاری فقط‌خواندنی فایل ROM، تعریف نقشه با منبع،
  ویرایش جدول با Undo/Redo، موتور چک‌سام و خروجی غیرمخرب به فایلی دیگر، CLI `bench` و تب داشبورد. هیچ
  ارتباطی با ECU وجود ندارد؛ تعریف نقشه واقعی برای خودروهای ایرانی هنوز نیست.
- **فاز ۵ (قفل):** خواندن/فلش Bench با پشتیبان‌گیری اجباری و بازگردانی (نیازمند تأیید صریح)
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
