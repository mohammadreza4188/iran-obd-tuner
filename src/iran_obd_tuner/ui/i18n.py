"""Persian/English strings of the dashboard.

Rules (enforced by ``tests/unit/test_i18n.py``):

* both languages have exactly the same keys, recursively;
* every ``{n}`` placeholder of a string exists in both languages;
* every key the window uses exists; no key is written twice.

This module imports no Qt, so the checks above run without the ``[gui]`` extra.
Trouble code descriptions are not translated here: they come from the knowledge database
(:mod:`iran_obd_tuner.database`), which is English only today.
"""

from __future__ import annotations

from typing import Any, Literal

__all__ = ["COPY", "LANGUAGES", "Lang", "format_message"]

Lang = Literal["fa", "en"]

LANGUAGES: tuple[Lang, ...] = ("fa", "en")

COPY: dict[str, dict[str, Any]] = {
    "fa": {
        "title": "داشبورد عیب‌یابی خودرو",
        "edition": "فاز ۱ — تشخیص زنده (فقط‌خواندنی)",
        "read_only_badge": "فقط‌خواندنی 🛡️",
        "tabs": {
            "dashboard": "داشبورد",
            "graph": "نمودار زنده",
            "diag": "کدهای خطا",
            "perf": "عملکرد",
            "ident": "شناسایی خودرو",
            "kb": "پایگاه دانش خودرو",
            "bench": "تیونینگ (Bench)",
            "settings": "تنظیمات",
            "log": "رویدادها",
        },
        "port": "درگاه",
        "refresh_ports": "بازخوانی فهرست درگاه‌ها",
        "auto": "— انتخاب درگاه —",
        "connect": "اتصال",
        "connecting": "در حال اتصال…",
        "disconnect": "قطع اتصال",
        "connected": "متصل",
        "idle": "قطع",
        "sim": "شبیه‌سازی",
        "page": "صفحه",
        "record_start": "شروع ضبط داده‌های زنده در CSV",
        "record_stop": "توقف ضبط",
        "recording_samples": "⏺ {0} نمونه",
        "sim_banner": ("⚠️ حالت شبیه‌سازی: همه مقادیر ساختگی‌اند و از هیچ خودرویی خوانده نشده‌اند."),
        "gauges": {
            "rpm": "دور موتور",
            "speed": "سرعت خودرو",
            "coolant": "دمای آب",
            "voltage": "ولتاژ",
            "fuel": "سطح سوخت",
            "throttle": "دریچه گاز",
            "iat": "دمای هوای ورودی",
            "load": "بار موتور",
        },
        "units": {
            "rpm": "RPM",
            "kmh": "km/h",
            "c": "°C",
            "v": "V",
            "pct": "%",
        },
        "pid_status": {
            "ok": "خوانده شد",
            "unsupported": "پشتیبانی نمی‌شود",
            "no_response": "بدون پاسخ",
            "malformed": "پاسخ نامعتبر",
            "no_data": "بدون داده",
            "fast_mode": "متوقف (poll سریع)",
        },
        "dtc_title": "کدهای خطا",
        "dtc_note": "توضیح کدها از یک کاتالوگ عمومی و غیرمرجع (انگلیسی) می‌آید.",
        "no_dtc": "کدی در این دسته گزارش نشد.",
        "dtc_not_connected": "برای خواندن کدهای خطا ابتدا متصل شوید.",
        "dtc_read_failed": "خواندن کدهای خطا ناموفق بود (این به معنی نبود کد خطا نیست): {0}",
        "dtc_unknown_description": "توضیح ناشناخته (در دیتابیس دانش نیست)",
        "clear_dtc": "پاک کردن کدها",
        "refresh_dtc": "خواندن مجدد",
        "ident_btn": "خواندن شناسه خودرو (VIN)",
        "ident_vin": "شماره شاسی (VIN):",
        "ident_protocol": "پروتکل:",
        "ident_adapter": "آداپتور:",
        "ident_unknown": "نامعلوم",
        "ident_simulated": "⚠️ داده شبیه‌سازی‌شده — این شناسه متعلق به هیچ خودرویی نیست.",
        "ident_real": "خوانده‌شده از خودرو",
        "ident_not_connected": "برای خواندن شناسه ابتدا متصل شوید.",
        "ident_failed": "خواندن شناسه خودرو ناموفق بود: {0}",
        "ident_obd_title": "شناسه از طریق OBD-II (سرویس 09)",
        "uds_title": "شناسایی ECU با UDS روی CAN (فقط‌خواندنی)",
        "uds_interface": "رابط CAN:",
        "uds_channel": "کانال:",
        "uds_channel_hint": "مثلاً COM5 یا PCAN_USBBUS1 یا can0",
        "uds_bitrate": "سرعت باس:",
        "uds_iface_sim": "ECU شبیه‌سازی‌شده (باس مجازی)",
        "uds_safety": (
            "⚠️ ایمنی: سوئیچ باز (ON)، موتور خاموش، خودرو کاملاً متوقف. فقط درخواست‌های "
            "خواندنی ارسال می‌شود؛ هیچ نوشتن، پاک کردن یا برنامه‌ریزی ECU وجود ندارد."
        ),
        "uds_extended": "اجازه جلسه Extended (0x10 0x03) برای شناسه‌هایی که در جلسه عادی رد شوند",
        "uds_extended_warning": (
            "پیش‌فرض خاموش است. فقط برای خواندن استفاده می‌شود و ECU همیشه به جلسه "
            "پیش‌فرض (0x10 0x01) برگردانده می‌شود. جلسه برنامه‌ریزی (0x02) همیشه مسدود است."
        ),
        "uds_discover_btn": "جستجوی ECUها (0x7DF، 3E 00)",
        "uds_read_btn": "خواندن شناسه ECU با UDS",
        "uds_banner_sim": "⚠️ ECU شبیه‌سازی‌شده — این مقادیر متعلق به هیچ خودرویی نیست.",
        "uds_banner_real": "رابط CAN واقعی — مقادیر از باس خوانده می‌شود.",
        "uds_busy": "در حال ارتباط...",
        "uds_discovered": "پاسخ‌دهنده به TesterPresent: {0}",
        "uds_discovered_none": "هیچ ECU به TesterPresent پاسخ نداد.",
        "uds_failed": "شناسایی UDS ناموفق بود: {0}",
        "uds_vin_line": "VIN از DID F190: {0}",
        "uds_ext_used": "جلسه Extended برای خواندن جایگزین استفاده شد و به جلسه پیش‌فرض برگشت.",
        "uds_ext_refused": "ECU جلسه Extended را نپذیرفت: {0}",
        "uds_session_ext": "(جلسه Extended)",
        "uds_dtc_title": "کدهای خطای UDS (0x19 0x02)",
        "uds_dtc_none": "کدهای خطای UDS: هیچ کدی گزارش نشد",
        "uds_dtc_unavailable": "کدهای خطای UDS در دسترس نیست: {0}",
        "uds_flag_sep": "، ",
        "uds_columns": {
            "did": "DID",
            "name": "عنوان",
            "value": "مقدار",
            "status": "وضعیت",
            "code": "کد",
            "dtc_status": "بایت وضعیت",
            "flags": "پرچم‌ها",
        },
        "uds_did_names": {
            "vin": "شماره شاسی (VIN)",
            "spare_part_number": "شماره قطعه",
            "ecu_software_version": "نسخه نرم‌افزار ECU",
            "ecu_hardware_number": "شماره سخت‌افزار ECU",
            "system_name": "نام سیستم",
        },
        "uds_flags": {
            "confirmed": "تأییدشده",
            "pending": "در انتظار",
            "test_failed": "تست ناموفق",
            "warning": "چراغ هشدار",
            "none": "بدون پرچم",
        },
        "bench_title": "میز کار و تیونینگ ECU",
        "bench_warning": (
            "این بخش در این نسخه پیاده‌سازی نشده است. هیچ خواندن ROM، نوشتن روی ECU یا "
            "دسترسی امنیتی وجود ندارد. هر قابلیت آینده نیازمند تأیید صریح صاحب پروژه، "
            "شواهد فنی و اعتبارسنجی کنترل‌شده است."
        ),
        "bench_btn_read": "خواندن ROM — پیاده‌سازی نشده",
        "bench_btn_write": "نوشتن روی ECU — پیاده‌سازی نشده",
        "bench_btn_unlock": "دسترسی امنیتی (Security Access) — پیاده‌سازی نشده",
        "safety_title": "هشدار ایمنی",
        "safety_heading": "🛑 عملیات مسدود شد",
        "safety_clear_dtc_msg": (
            "پاک کردن کدهای خطا وضعیت خودرو را تغییر می‌دهد و در این نسخه فقط‌خواندنی "
            "پیاده‌سازی نشده است. هیچ فرمانی به خودرو ارسال نشد."
        ),
        "safety_ack": "متوجه شدم",
        "settings_lang": "زبان",
        "settings_mode": "حالت اتصال",
        "mode_real": "آداپتور واقعی (ELM327)",
        "mode_sim": "شبیه‌سازی (بدون سخت‌افزار)",
        "sim_note": (
            "برای آداپتور واقعی ELM327، درگاه سریال آن را در نوار ابزار صریحاً انتخاب کنید "
            "(جستجوی خودکار درگاه غیرفعال است). این آداپتور فقط می‌خواند و هنوز روی "
            "سخت‌افزار واقعی اعتبارسنجی نشده است. بدون سخت‌افزار، حالت شبیه‌سازی را انتخاب کنید."
        ),
        "adapter_unavailable": "آداپتور انتخاب‌شده آماده نیست:\n{0}",
        "connection_failed": "اتصال برقرار نشد:\n{0}",
        "recording_failed": "شروع ضبط ناموفق بود:\n{0}",
        "save_dialog_title": "ذخیره داده‌های زنده",
        "save_dialog_filter": "فایل CSV (*.csv)",
        "startup_failed": "راه‌اندازی برنامه ناموفق بود",
        "mil": {
            "disconnected": "چراغ چک: —",
            "on": "🔴 چراغ چک روشن ({0} کد)",
            "off": "🟢 چراغ چک خاموش",
            "unknown": "چراغ چک: نامعلوم",
        },
        "mil_incomplete": "مانیتورهای آمادگی کامل‌نشده: {0}",
        "dtc_filter_all": "همه دسته‌ها",
        "dtc_category": {
            "stored": "ذخیره‌شده",
            "pending": "در انتظار",
            "permanent": "دائمی",
        },
        "dtc_category_header": "{0} (سرویس {1}) — {2} کد",
        "dtc_category_unavailable": "{0} (سرویس {1}) — {2}",
        "outcome": {
            "ok": "خوانده شد",
            "unsupported": "پشتیبانی نمی‌شود",
            "no_response": "بدون پاسخ (به معنی نبود کد نیست)",
            "error": "پاسخ نامعتبر",
        },
        "ff_title": "Freeze Frame (وضعیت لحظه ثبت خطا)",
        "ff_read_btn": "خواندن Freeze Frame",
        "ff_not_read": "Freeze Frame هنوز خوانده نشده است.",
        "ff_not_connected": "برای خواندن Freeze Frame ابتدا متصل شوید.",
        "ff_none_stored": "خودرو گزارش داد که هیچ Freeze Frame ذخیره نشده است.",
        "ff_unavailable": "Freeze Frame در دسترس نیست: {0}",
        "ff_trigger": "کد عامل: {0} — {1}",
        "ff_columns": {
            "param": "پارامتر",
            "value": "مقدار",
            "unit": "واحد",
            "status": "وضعیت",
        },
        "ff_params": {
            "0104": "بار موتور",
            "0105": "دمای آب",
            "0106": "اصلاح کوتاه‌مدت سوخت (بانک ۱)",
            "0107": "اصلاح بلندمدت سوخت (بانک ۱)",
            "010C": "دور موتور",
            "010D": "سرعت خودرو",
            "010F": "دمای هوای ورودی",
            "0111": "دریچه گاز",
        },
        "log_tab_events": "رویدادها",
        "log_tab_traffic": "ترافیک خام آداپتور",
        "traffic_pause": "توقف نمایش",
        "traffic_resume": "ادامه نمایش",
        "traffic_clear": "پاک کردن",
        "traffic_copy": "کپی در کلیپ‌بورد",
        "traffic_privacy": (
            "این خطوط ممکن است شماره شاسی (VIN) خودرو را داشته باشند؛ پیش از انتشار عمومی "
            "آن‌ها را بررسی کنید."
        ),
        "traffic_stats": "{0} خط، {1} خط قدیمی حذف‌شده (ظرفیت {2})",
        "traffic_sim_tag": "[شبیه‌سازی]",
        "perf_fast_poll": (
            "poll سریع هنگام اندازه‌گیری (فقط دور موتور، سرعت و دریچه گاز؛ دستور کمتر، نه بیشتر)"
        ),
        "perf_estimated": "تخمینی / محاسباتی",
        "perf_simulated": "[داده شبیه‌سازی‌شده]",
        "timer_title": "زمان‌سنج ۰ تا ۱۰۰ کیلومتر بر ساعت (تخمینی)",
        "timer_arm": "آماده‌سازی",
        "timer_reset": "ریست",
        "timer_state": {
            "idle": "غیرفعال — «آماده‌سازی» را بزنید",
            "armed": "منتظر توقف کامل خودرو…",
            "armed_at_standstill": "آماده — حرکت کنید",
            "running": "در حال اندازه‌گیری…",
            "completed": "تمام شد",
            "aborted": "لغو شد: {0}",
        },
        "timer_abort": {
            "speed_drop": "سرعت کم شد",
            "sample_gap": "داده سرعت قطع شد",
            "timeout": "بیش از حد طول کشید",
            "disconnected": "اتصال قطع شد",
        },
        "timer_result": "۰ تا {0}: {1} ثانیه — ۰ تا {2}: {3} ثانیه — مسافت: {4} متر",
        "timer_quality": "عدم‌قطعیت ±{0} ثانیه — بیشترین فاصله نمونه‌ها {1} ثانیه — {2} نمونه",
        "timer_note": (
            "بر اساس سرعتی که ECU گزارش می‌دهد (دقت ۱ km/h و چند نمونه در ثانیه)؛ با زمان‌سنج "
            "دقیق یا GPS برابر نیست."
        ),
        "dyno_title": "دینوی مجازی",
        "dyno_warning": "تخمینی / محاسباتی — دینامومتر واقعی نیست",
        "dyno_note": (
            "مقادیر پیش‌فرض عمومی‌اند و مشخصات هیچ خودروی خاصی نیستند؛ وزن خالی را از مدارک "
            "خودرو وارد کنید. فرض‌ها: جاده صاف، بدون باد، یک دنده، دریچه گاز کامل. خطای وزن، "
            "Cd، سطح مقطع یا تلفات انتقال مستقیماً در نتیجه اثر می‌گذارد."
        ),
        "dyno_params": {
            "curb_mass_kg": "وزن خالی خودرو (kg)",
            "extra_mass_kg": "وزن سرنشین و بار (kg)",
            "drag_coefficient": "ضریب پسا (Cd)",
            "frontal_area_m2": "سطح مقطع جلو (m²)",
            "drivetrain_loss": "تلفات انتقال قدرت (٪)",
            "rolling_resistance": "ضریب مقاومت غلتشی",
            "air_density_kg_m3": "چگالی هوا (kg/m³)",
        },
        "dyno_mass_unset": "وارد نشده",
        "dyno_start": "شروع ثبت کشش",
        "dyno_finish": "پایان و محاسبه",
        "dyno_cancel": "لغو",
        "dyno_status": {
            "idle": "آماده. در یک دنده با دریچه گاز کامل شتاب بگیرید.",
            "recording": "در حال ثبت… ({0} نمونه)",
            "done": "محاسبه شد",
            "error": "محاسبه ممکن نشد: {0}",
        },
        "dyno_peaks": (
            "بیشینه توان: {0} اسب بخار ({1} kW) در {2} rpm — بیشینه گشتاور: {3} N·m در {4} rpm"
        ),
        "dyno_quality": "کیفیت: {0} نمونه، بیشترین فاصله {1} ثانیه، R² نسبت دنده {2}",
        "dyno_quality_low": "⚠️ کیفیت پایین: {0}",
        "dyno_quality_reasons": {
            "few_samples": "نمونه کم",
            "gear_fit": "نسبت سرعت به دور ثابت نیست",
            "slow_sampling": "نمونه‌برداری کند",
            "throttle_unknown": "دریچه گاز نامعلوم",
        },
        "dyno_chart_rpm": "دور موتور (rpm)",
        "dyno_chart_power": "توان (اسب بخار)",
        "dyno_chart_torque": "گشتاور (N·m)",
        "kb_title": "پایگاه دانش خودروها و ECUهای بازار ایران (فقط‌خواندنی)",
        "kb_source": "منبع داده: {0}",
        "kb_honesty": (
            "هر ادعا سطح اطمینان و منبع دارد. «نامعلوم» یعنی هیچ منبع قابل استنادی پیدا نشده"
            " است؛ هیچ مقداری حدس زده نمی‌شود. «تأییدشده» فقط با بسته شواهد میدانی از خودروی"
            " واقعی ممکن است."
        ),
        "kb_legend": "سطح اطمینان:",
        "kb_confidence": {
            "verified": "تأییدشده",
            "documented": "مستند",
            "reported": "گزارش‌شده",
            "unverified": "تأییدنشده",
            "unknown": "نامعلوم",
        },
        "kb_manufacturer": "سازنده",
        "kb_all_manufacturers": "همه سازندگان",
        "kb_select_hint": "یک خودرو را از فهرست انتخاب کنید.",
        "kb_variant_header": "{0} — {1}",
        "kb_columns": {
            "field": "ویژگی",
            "value": "مقدار",
            "confidence": "اطمینان",
            "source": "منبع",
            "notes": "توضیح / روش بررسی",
        },
        "kb_sections": {"vehicle": "خودرو", "obd_port": "درگاه OBD", "ecu": "ECU ({0})"},
        "kb_fields": {
            "engine": "موتور",
            "model_years": "سال‌های مدل",
            "obd_physical_layer": "لایه فیزیکی / پروتکل OBD",
            "can_bitrate_bps": "سرعت CAN",
            "supported_pids": "PIDهای پشتیبانی‌شده",
            "family_candidates": "خانواده ECU (نامزدها)",
            "supplier": "سازنده ECU",
            "diagnostic_protocol": "پروتکل عیب‌یابی",
            "request_id": "شناسه درخواست (TX)",
            "response_id": "شناسه پاسخ (RX)",
            "id_bits": "طول شناسه CAN",
            "part_number_patterns": "الگوی شماره قطعه (F187)",
            "software_version_patterns": "الگوی نسخه نرم‌افزار (F189)",
            "system_name_patterns": "الگوی نام سیستم (F197)",
            "dids": "DIDهای UDS",
            "manufacturer_pids": "PIDهای اختصاصی سازنده",
        },
        "kb_unknown_value": "نامعلوم",
        "kb_nothing_recorded": "چیزی ثبت نشده (نامعلوم)",
        "kb_verify_prefix": "روش بررسی: {0}",
        "kb_evidence": "شواهد: {0}",
        "kb_match_title": "تطبیق با جلسه فعلی (غیرفعال؛ هیچ درخواستی ارسال نمی‌شود)",
        "kb_match_btn": "تطبیق با داده‌های جلسه",
        "kb_match_unavailable": "ابتدا اتصال OBD یا شناسایی UDS انجام دهید.",
        "kb_match_none": (
            "هیچ پروفایلی ادعای قابل مقایسه ندارد؛ بیشتر ادعاهای باس و ECU هنوز نامعلوم‌اند و"
            " به شواهد میدانی نیاز دارند."
        ),
        "kb_match_summary": "{0} نامزد، {1} حذف‌شده، {2} بدون شواهد قابل مقایسه",
        "kb_match_disclaimer": "نامزدها با مشاهدات سازگارند؛ این یک شناسایی قطعی نیست.",
        "kb_match_simulated": "⚠️ مشاهدات شبیه‌سازی‌شده‌اند — هیچ خودرویی خوانده نشده است.",
        "kb_match_failed": "تطبیق ناموفق: {0}",
        "kb_match_columns": {
            "rank": "رتبه",
            "vehicle": "خودرو",
            "tier": "سطح",
            "score": "امتیاز",
            "signals": "نشانه‌ها",
        },
        "kb_tiers": {
            "strong": "قوی",
            "partial": "جزئی",
            "weak": "ضعیف (فقط سازگار)",
            "no_evidence": "بدون شواهد",
            "excluded": "حذف‌شده",
        },
        "kb_signals": {
            "vin_wmi": "WMI شماره شاسی",
            "part_number": "شماره قطعه",
            "software_version": "نسخه نرم‌افزار",
            "system_name": "نام سیستم",
            "physical_layer": "لایه فیزیکی",
            "supported_pids": "PIDها",
        },
        "kb_outcomes": {"match": "منطبق", "conflict": "مغایر"},
        "kb_export_title": "خروجی بسته شواهد میدانی (Field Evidence Bundle)",
        "kb_export_note": (
            "پروتکل، PIDهای پشتیبانی‌شده، DIDهای شناسایی UDS و بخشی از ترافیک خام در یک فایل"
            " JSON ذخیره می‌شوند تا ادعاهای «نامعلوم» پس از بازبینی به «تأییدشده» ارتقا یابند."
            " فقط VIN پنهان می‌شود؛ پیش از اشتراک‌گذاری فایل را بررسی کنید."
        ),
        "kb_claimed_vehicle": "این خودرو به گفته اپراتور:",
        "kb_claimed_none": "— مشخص نشده —",
        "kb_model_year": "سال مدل:",
        "kb_operator_notes": "یادداشت (مثلاً متن برچسب ECU):",
        "kb_redact": "پنهان کردن ۶ نویسه آخر VIN (شماره سریال)",
        "kb_export_btn": "ذخیره بسته شواهد…",
        "kb_export_dialog": "ذخیره بسته شواهد میدانی",
        "kb_export_filter": "JSON (*.json)",
        "kb_export_done": "بسته ذخیره شد: {0}",
        "kb_export_done_sim": "⚠️ بسته شبیه‌سازی‌شده ذخیره شد (برای تأیید قابل استفاده نیست): {0}",
        "kb_export_failed": "خروجی ناموفق: {0}",
        "messages": {
            "msg.connected": "اتصال برقرار شد: {0} ({1})",
            "msg.disconnected": "اتصال قطع شد",
            "msg.connect_error": "خطای اتصال: {0}",
            "msg.adapter_unavailable": "آداپتور در دسترس نیست: {0}",
            "msg.connection_lost": "ارتباط با آداپتور قطع شد: {0}",
            "msg.dtc_read": "{0} کد خطا خوانده شد",
            "msg.ff_read": "Freeze Frame خوانده شد: {0}",
            "msg.ff_error": "خطای خواندن Freeze Frame: {0}",
            "msg.dtc_read_error": "خطای خواندن کدهای خطا: {0}",
            "msg.clear_dtc_refused": "درخواست پاک کردن کدها توسط گارد ایمنی رد شد",
            "msg.ident_read": "شناسه خودرو خوانده شد: {0}",
            "msg.ident_error": "خطای خواندن شناسه خودرو: {0}",
            "msg.uds_read": "شناسه ECU با UDS خوانده شد ({0}): VIN {1}",
            "msg.uds_error": "خطای شناسایی UDS: {0}",
            "msg.kb_match": "تطبیق خودرو: {0} نامزد",
            "msg.kb_error": "خطای پایگاه دانش خودرو: {0}",
            "msg.kb_export": "بسته شواهد میدانی ذخیره شد: {0}",
            "msg.kb_export_error": "خطای خروجی بسته شواهد: {0}",
            "msg.uds_discovered": "جستجوی ECU: {0}",
            "msg.port_scan": "بازخوانی درگاه‌ها: {0} → {1} گزینه",
            "msg.recording_started": "ضبط شروع شد: {0}",
            "msg.recording_stopped": "ضبط متوقف شد ({0} نمونه): {1}",
            "msg.recording_error": "خطای ضبط: {0}",
        },
    },
    "en": {
        "title": "Vehicle Diagnostics Dashboard",
        "edition": "Phase 1 — live diagnostics (read-only)",
        "read_only_badge": "READ-ONLY 🛡️",
        "tabs": {
            "dashboard": "Dashboard",
            "graph": "Live Graph",
            "diag": "Trouble Codes",
            "perf": "Performance",
            "ident": "Identification",
            "kb": "Vehicle knowledge",
            "bench": "Bench / Tuning",
            "settings": "Settings",
            "log": "Events",
        },
        "port": "Port",
        "refresh_ports": "Refresh the port list",
        "auto": "— select a port —",
        "connect": "CONNECT",
        "connecting": "CONNECTING…",
        "disconnect": "DISCONNECT",
        "connected": "Connected",
        "idle": "Offline",
        "sim": "Simulation",
        "page": "Page",
        "record_start": "Start recording live data to CSV",
        "record_stop": "Stop recording",
        "recording_samples": "⏺ {0} samples",
        "sim_banner": "⚠️ Simulation mode: every value is simulated, none is read from a vehicle.",
        "gauges": {
            "rpm": "Engine RPM",
            "speed": "Vehicle Speed",
            "coolant": "Coolant Temp",
            "voltage": "Voltage",
            "fuel": "Fuel Level",
            "throttle": "Throttle",
            "iat": "Intake Air Temp",
            "load": "Engine Load",
        },
        "units": {
            "rpm": "RPM",
            "kmh": "km/h",
            "c": "°C",
            "v": "V",
            "pct": "%",
        },
        "pid_status": {
            "ok": "Read",
            "unsupported": "Not supported",
            "no_response": "No response",
            "malformed": "Malformed response",
            "no_data": "No data",
            "fast_mode": "Paused (fast polling)",
        },
        "dtc_title": "Trouble codes",
        "dtc_note": "Descriptions come from a generic, non-authoritative catalogue (English).",
        "no_dtc": "No codes reported in this category.",
        "dtc_not_connected": "Connect first to read trouble codes.",
        "dtc_read_failed": "Reading trouble codes failed (this does not mean there are none): {0}",
        "dtc_unknown_description": "description unknown (not in the knowledge database)",
        "clear_dtc": "Clear codes",
        "refresh_dtc": "Refresh",
        "ident_btn": "Read vehicle identification (VIN)",
        "ident_vin": "VIN:",
        "ident_protocol": "Protocol:",
        "ident_adapter": "Adapter:",
        "ident_unknown": "unknown",
        "ident_simulated": "⚠️ Simulated data — this identification belongs to no vehicle.",
        "ident_real": "Read from the vehicle",
        "ident_not_connected": "Connect first to read the identification.",
        "ident_failed": "Reading the vehicle identification failed: {0}",
        "ident_obd_title": "Identification over OBD-II (service 09)",
        "uds_title": "UDS ECU identification over CAN (read-only)",
        "uds_interface": "CAN interface:",
        "uds_channel": "Channel:",
        "uds_channel_hint": "e.g. COM5, PCAN_USBBUS1 or can0",
        "uds_bitrate": "Bitrate:",
        "uds_iface_sim": "Simulated ECU (virtual bus)",
        "uds_safety": (
            "⚠️ Safety: ignition ON, engine OFF, vehicle stationary. Only read requests are "
            "sent; there is no writing, clearing or ECU programming."
        ),
        "uds_extended": "Allow the extended session (0x10 0x03) for identifiers the default "
        "session refuses",
        "uds_extended_warning": (
            "Off by default. Used for reading only, and the ECU is always returned to the "
            "default session (0x10 0x01). The programming session (0x02) is always blocked."
        ),
        "uds_discover_btn": "Discover ECUs (0x7DF, 3E 00)",
        "uds_read_btn": "Read ECU identification (UDS)",
        "uds_banner_sim": "⚠️ Simulated ECU — these values belong to no vehicle.",
        "uds_banner_real": "Real CAN interface — values are read from the bus.",
        "uds_busy": "Communicating...",
        "uds_discovered": "Answered TesterPresent: {0}",
        "uds_discovered_none": "No ECU answered TesterPresent.",
        "uds_failed": "UDS identification failed: {0}",
        "uds_vin_line": "VIN from DID F190: {0}",
        "uds_ext_used": "The extended session was used for a fallback read, then left.",
        "uds_ext_refused": "The ECU refused the extended session: {0}",
        "uds_session_ext": "(extended session)",
        "uds_dtc_title": "UDS trouble codes (0x19 0x02)",
        "uds_dtc_none": "UDS trouble codes: none reported",
        "uds_dtc_unavailable": "UDS trouble codes not available: {0}",
        "uds_flag_sep": ", ",
        "uds_columns": {
            "did": "DID",
            "name": "Name",
            "value": "Value",
            "status": "Status",
            "code": "Code",
            "dtc_status": "Status byte",
            "flags": "Flags",
        },
        "uds_did_names": {
            "vin": "VIN",
            "spare_part_number": "Spare part number",
            "ecu_software_version": "ECU software version",
            "ecu_hardware_number": "ECU hardware number",
            "system_name": "System name",
        },
        "uds_flags": {
            "confirmed": "confirmed",
            "pending": "pending",
            "test_failed": "test failed",
            "warning": "warning lamp",
            "none": "no flag set",
        },
        "bench_title": "ECU Bench & Tuning",
        "bench_warning": (
            "This section is not implemented in this build. There is no ROM reading, no ECU "
            "writing and no security access. Any future capability requires explicit owner "
            "approval, technical evidence and controlled validation."
        ),
        "bench_btn_read": "Read ROM — not implemented",
        "bench_btn_write": "Write to ECU — not implemented",
        "bench_btn_unlock": "Security Access — not implemented",
        "safety_title": "Safety warning",
        "safety_heading": "🛑 Operation blocked",
        "safety_clear_dtc_msg": (
            "Clearing trouble codes changes vehicle state and is not implemented in this "
            "read-only build. No command was sent to the vehicle."
        ),
        "safety_ack": "Understood",
        "settings_lang": "Language",
        "settings_mode": "Connection mode",
        "mode_real": "Real adapter (ELM327)",
        "mode_sim": "Simulation (no hardware)",
        "sim_note": (
            "For a real ELM327 adapter, select its serial port explicitly in the toolbar "
            "(automatic port probing is disabled). The adapter only reads and has not been "
            "validated on real hardware yet. Without hardware, use simulation mode."
        ),
        "adapter_unavailable": "The selected adapter is not ready:\n{0}",
        "connection_failed": "Connection failed:\n{0}",
        "recording_failed": "Recording could not be started:\n{0}",
        "save_dialog_title": "Save live data",
        "save_dialog_filter": "CSV files (*.csv)",
        "startup_failed": "Startup failed",
        "mil": {
            "disconnected": "MIL: —",
            "on": "🔴 MIL ON ({0} codes)",
            "off": "🟢 MIL off",
            "unknown": "MIL: unknown",
        },
        "mil_incomplete": "Readiness monitors not complete: {0}",
        "dtc_filter_all": "All categories",
        "dtc_category": {
            "stored": "Stored",
            "pending": "Pending",
            "permanent": "Permanent",
        },
        "dtc_category_header": "{0} (service {1}) — {2} code(s)",
        "dtc_category_unavailable": "{0} (service {1}) — {2}",
        "outcome": {
            "ok": "read",
            "unsupported": "not supported",
            "no_response": "no answer (this does not mean there are none)",
            "error": "malformed answer",
        },
        "ff_title": "Freeze frame (conditions when the fault was stored)",
        "ff_read_btn": "Read freeze frame",
        "ff_not_read": "The freeze frame has not been read yet.",
        "ff_not_connected": "Connect first to read the freeze frame.",
        "ff_none_stored": "The vehicle reports that no freeze frame is stored.",
        "ff_unavailable": "Freeze frame not available: {0}",
        "ff_trigger": "Triggered by: {0} — {1}",
        "ff_columns": {
            "param": "Parameter",
            "value": "Value",
            "unit": "Unit",
            "status": "Status",
        },
        "ff_params": {
            "0104": "Engine load",
            "0105": "Coolant temperature",
            "0106": "Short term fuel trim (bank 1)",
            "0107": "Long term fuel trim (bank 1)",
            "010C": "Engine speed",
            "010D": "Vehicle speed",
            "010F": "Intake air temperature",
            "0111": "Throttle position",
        },
        "log_tab_events": "Events",
        "log_tab_traffic": "Raw adapter traffic",
        "traffic_pause": "Pause view",
        "traffic_resume": "Resume view",
        "traffic_clear": "Clear",
        "traffic_copy": "Copy to clipboard",
        "traffic_privacy": (
            "These lines may contain the vehicle's VIN; review them before sharing publicly."
        ),
        "traffic_stats": "{0} lines, {1} older lines dropped (capacity {2})",
        "traffic_sim_tag": "[SIMULATED]",
        "perf_fast_poll": (
            "Fast polling while measuring (rpm, speed and throttle only: fewer commands, not more)"
        ),
        "perf_estimated": "estimated / calculated",
        "perf_simulated": "[simulated data]",
        "timer_title": "0–100 km/h timer (estimated)",
        "timer_arm": "Arm",
        "timer_reset": "Reset",
        "timer_state": {
            "idle": "Idle — press Arm",
            "armed": "Waiting for the vehicle to stand still…",
            "armed_at_standstill": "Ready — go",
            "running": "Measuring…",
            "completed": "Completed",
            "aborted": "Aborted: {0}",
        },
        "timer_abort": {
            "speed_drop": "speed dropped",
            "sample_gap": "speed data stopped",
            "timeout": "took too long",
            "disconnected": "disconnected",
        },
        "timer_result": "0–{0}: {1} s — 0–{2}: {3} s — distance: {4} m",
        "timer_quality": "uncertainty ±{0} s — largest sample interval {1} s — {2} samples",
        "timer_note": (
            "Based on the vehicle speed the ECU reports (1 km/h resolution, a few samples per "
            "second); not equivalent to a timing gate or GPS."
        ),
        "dyno_title": "Virtual dyno",
        "dyno_warning": "Estimated / calculated — not a real dynamometer",
        "dyno_note": (
            "Defaults are generic, not the specification of any vehicle; enter the curb mass "
            "from the vehicle's documents. Assumes a flat road, no wind, one gear and full "
            "throttle. Errors in mass, Cd, frontal area or drivetrain loss go straight into "
            "the result."
        ),
        "dyno_params": {
            "curb_mass_kg": "Curb mass (kg)",
            "extra_mass_kg": "Occupants and cargo (kg)",
            "drag_coefficient": "Drag coefficient (Cd)",
            "frontal_area_m2": "Frontal area (m²)",
            "drivetrain_loss": "Drivetrain loss (%)",
            "rolling_resistance": "Rolling resistance coefficient",
            "air_density_kg_m3": "Air density (kg/m³)",
        },
        "dyno_mass_unset": "not entered",
        "dyno_start": "Start recording a pull",
        "dyno_finish": "Finish and calculate",
        "dyno_cancel": "Cancel",
        "dyno_status": {
            "idle": "Ready. Accelerate at full throttle in one gear.",
            "recording": "Recording… ({0} samples)",
            "done": "Calculated",
            "error": "Could not calculate: {0}",
        },
        "dyno_peaks": "Peak power: {0} PS ({1} kW) at {2} rpm — peak torque: {3} N·m at {4} rpm",
        "dyno_quality": "Quality: {0} samples, largest interval {1} s, gear-ratio R² {2}",
        "dyno_quality_low": "⚠️ Low quality: {0}",
        "dyno_quality_reasons": {
            "few_samples": "few samples",
            "gear_fit": "speed/rpm ratio not constant",
            "slow_sampling": "slow sampling",
            "throttle_unknown": "throttle unknown",
        },
        "dyno_chart_rpm": "Engine speed (rpm)",
        "dyno_chart_power": "Power (PS)",
        "dyno_chart_torque": "Torque (N·m)",
        "kb_title": "Iranian vehicle and ECU knowledge base (read-only)",
        "kb_source": "Data: {0}",
        "kb_honesty": (
            "Every claim carries a confidence level and a source. 'Unknown' means no citable"
            " source was found; nothing is guessed. 'Verified' needs a Field Evidence Bundle"
            " captured on a real vehicle."
        ),
        "kb_legend": "Confidence:",
        "kb_confidence": {
            "verified": "Verified",
            "documented": "Documented",
            "reported": "Reported",
            "unverified": "Unverified",
            "unknown": "Unknown",
        },
        "kb_manufacturer": "Manufacturer",
        "kb_all_manufacturers": "All manufacturers",
        "kb_select_hint": "Select a vehicle variant from the list.",
        "kb_variant_header": "{0} — {1}",
        "kb_columns": {
            "field": "Field",
            "value": "Value",
            "confidence": "Confidence",
            "source": "Source",
            "notes": "Notes / how to verify",
        },
        "kb_sections": {"vehicle": "Vehicle", "obd_port": "OBD port", "ecu": "ECU ({0})"},
        "kb_fields": {
            "engine": "Engine",
            "model_years": "Model years",
            "obd_physical_layer": "OBD physical layer / protocol",
            "can_bitrate_bps": "CAN bitrate",
            "supported_pids": "Supported PIDs",
            "family_candidates": "ECU family (candidates)",
            "supplier": "ECU supplier",
            "diagnostic_protocol": "Diagnostic protocol",
            "request_id": "Request ID (TX)",
            "response_id": "Response ID (RX)",
            "id_bits": "CAN ID length",
            "part_number_patterns": "Part number pattern (F187)",
            "software_version_patterns": "Software version pattern (F189)",
            "system_name_patterns": "System name pattern (F197)",
            "dids": "UDS DIDs",
            "manufacturer_pids": "Manufacturer PIDs",
        },
        "kb_unknown_value": "unknown",
        "kb_nothing_recorded": "nothing recorded (unknown)",
        "kb_verify_prefix": "How to verify: {0}",
        "kb_evidence": "Evidence: {0}",
        "kb_match_title": "Match the current session (passive; nothing is sent)",
        "kb_match_btn": "Match session data",
        "kb_match_unavailable": "Connect (OBD) or read a UDS identification first.",
        "kb_match_none": (
            "No profile has comparable claims yet; most bus and ECU facts are still unknown and"
            " need field evidence."
        ),
        "kb_match_summary": "{0} candidate(s), {1} excluded, {2} without comparable evidence",
        "kb_match_disclaimer": (
            "Candidates are consistent with the observations; this is not an identification."
        ),
        "kb_match_simulated": "⚠️ Simulated observations: no vehicle was read.",
        "kb_match_failed": "Match failed: {0}",
        "kb_match_columns": {
            "rank": "#",
            "vehicle": "Vehicle",
            "tier": "Tier",
            "score": "Score",
            "signals": "Signals",
        },
        "kb_tiers": {
            "strong": "Strong",
            "partial": "Partial",
            "weak": "Weak (consistent only)",
            "no_evidence": "No evidence",
            "excluded": "Excluded",
        },
        "kb_signals": {
            "vin_wmi": "VIN WMI",
            "part_number": "Part number",
            "software_version": "Software version",
            "system_name": "System name",
            "physical_layer": "Physical layer",
            "supported_pids": "PIDs",
        },
        "kb_outcomes": {"match": "match", "conflict": "conflict"},
        "kb_export_title": "Export a Field Evidence Bundle",
        "kb_export_note": (
            "Saves the protocol, supported PIDs, UDS identification DIDs and an excerpt of the raw"
            " traffic as one JSON file, so that 'unknown' claims can be promoted to 'verified'"
            " after review. Only the VIN is redacted; review the file before sharing it."
        ),
        "kb_claimed_vehicle": "Operator says this vehicle is:",
        "kb_claimed_none": "— not specified —",
        "kb_model_year": "Model year:",
        "kb_operator_notes": "Notes (e.g. ECU label text):",
        "kb_redact": "Mask the last 6 VIN characters (serial number)",
        "kb_export_btn": "Save evidence bundle…",
        "kb_export_dialog": "Save Field Evidence Bundle",
        "kb_export_filter": "JSON (*.json)",
        "kb_export_done": "Bundle saved: {0}",
        "kb_export_done_sim": "⚠️ SIMULATED bundle saved (cannot verify anything): {0}",
        "kb_export_failed": "Export failed: {0}",
        "messages": {
            "msg.connected": "Connected: {0} ({1})",
            "msg.disconnected": "Disconnected",
            "msg.connect_error": "Connect error: {0}",
            "msg.adapter_unavailable": "Adapter not available: {0}",
            "msg.connection_lost": "Connection to the adapter lost: {0}",
            "msg.dtc_read": "{0} trouble code(s) read",
            "msg.ff_read": "Freeze frame read: {0}",
            "msg.ff_error": "Freeze frame read error: {0}",
            "msg.dtc_read_error": "Trouble code read error: {0}",
            "msg.clear_dtc_refused": "Clear-codes request refused by the safety guard",
            "msg.ident_read": "Vehicle identification read: {0}",
            "msg.ident_error": "Vehicle identification error: {0}",
            "msg.uds_read": "UDS ECU identification read ({0}): VIN {1}",
            "msg.uds_error": "UDS identification error: {0}",
            "msg.kb_match": "Vehicle match: {0} candidate(s)",
            "msg.kb_error": "Vehicle knowledge error: {0}",
            "msg.kb_export": "Field Evidence Bundle saved: {0}",
            "msg.kb_export_error": "Evidence bundle export failed: {0}",
            "msg.uds_discovered": "ECU discovery: {0}",
            "msg.port_scan": "Port list refreshed: {0} -> {1} entries",
            "msg.recording_started": "Recording started: {0}",
            "msg.recording_stopped": "Recording stopped ({0} samples): {1}",
            "msg.recording_error": "Recording error: {0}",
        },
    },
}


def format_message(lang: Lang, key: str, *args: object) -> str:
    """Return the translated log/status message ``key`` with ``args`` substituted.

    An unknown key is returned unchanged (and caught by the i18n tests) instead of crashing
    the window.
    """
    template = COPY[lang]["messages"].get(key)
    if template is None:
        return key
    return template.format(*args)
