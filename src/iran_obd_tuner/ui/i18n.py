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
        "bench_title": "میز کار ROM (تحلیل آفلاین فایل)",
        "bench_warning": (
            "میز کار ROM: فقط تحلیل آفلاین فایل؛ از اینجا هیچ داده‌ای از ECU خوانده یا روی آن"
            " نوشته نمی‌شود و فایل خروجی برای هیچ خودرو یا ECU تأیید نشده است. فلش Bench پایین"
            " صفحه فقط روی ECU شبیه‌سازی‌شده کار می‌کند."
        ),
        "bflash_title": "فلش Bench شبیه‌سازی‌شده (فاز ۵)",
        "bflash_sim_badge": "SIMULATED — فقط ECU مجازی",
        "bflash_intro": (
            "این بخش فرایند چهارمرحله‌ای فلش و بازیابی را فقط روی ECU برنامه‌پذیر مجازی اجرا"
            " می‌کند. هیچ ECU، آداپتور یا درگاه واقعی در دسترس نیست و هیچ گزینه‌ای برای انتخاب"
            " آن وجود ندارد."
        ),
        "bflash_mode_inactive": "حالت فلش Bench غیرفعال است.",
        "bflash_mode_active": "حالت فلش Bench برای همین پنجره فعال است (اپراتور: {0}).",
        "bflash_enable": "فعال‌سازی حالت فلش Bench…",
        "bflash_disable": "خروج از حالت فلش Bench",
        "bflash_source_demo": "تصویر: ROM نمایشی SIMULATED (همراه با تعریف چک‌سام‌ها)",
        "bflash_source_file": "تصویر: {0} — تعریف‌ها: {1}",
        "bflash_no_maps": "ندارد",
        "bflash_choose_rom": "انتخاب فایل ROM…",
        "bflash_use_demo": "استفاده از ROM نمایشی",
        "bflash_allow_unverified": ("اجازه چک‌سام تأییدنشده (فقط شبیه‌سازی؛ در ژورنال ثبت می‌شود)"),
        "bflash_fault_label": "خطای شبیه‌ساز (آموزشی):",
        "bflash_faults": {
            "none": "بدون خطا",
            "power_loss": "قطع برق در بلوک ۲۰",
            "drop": "قطع ارتباط در بلوک ۳",
            "nrc72": "پاسخ منفی 0x72 در بلوک ۲",
            "bad_key": "کلید امنیتی نادرست",
            "corrupt_readback": "خراب شدن بازخوانی مرحله ۴",
        },
        "bflash_start": "شروع فلش شبیه‌سازی‌شده",
        "bflash_cancel": "لغو",
        "bflash_discard": "کنار گذاشتن ژورنال کهنه…",
        "bflash_inspect": "بررسی ژورنال",
        "bflash_do_not_disconnect": (
            "⚠️ پاک‌سازی حافظه آغاز شده است. اتصال را قطع نکنید و برنامه را نبندید؛ لغو دیگر"
            " ممکن نیست. اگر فرایند قطع شود، ژورنال بازیابی را ممکن می‌کند."
        ),
        "bflash_journal_label": "ژورنال:",
        "bflash_dir_label": "پوشه Bench:",
        "bflash_backup_label": "فایل پشتیبان:",
        "bflash_sha_label": "SHA-256 پشتیبان:",
        "bflash_steps": {
            "pre_flight": "پیش‌پرواز",
            "backup": "پشتیبان کامل اجباری",
            "programming": "انتقال برنامه",
            "verify": "بازبینی",
        },
        "bflash_idle": "آماده.",
        "bflash_progress_bytes": "{0}: {1}/{2} بایت ({3}٪)",
        "bflash_progress_block": "بلوک {0}",
        "bflash_journal_unknown": "هنوز بررسی نشده",
        "bflash_journal_status": {
            "clean": "تمیز",
            "stale": "کهنه (ECU دست‌نخورده)",
            "dirty": "ناتمام — بازیابی لازم است",
            "corrupt": "خراب — بررسی دستی لازم است",
        },
        "bflash_states": {
            "pending": "در انتظار (ECU دست‌نخورده)",
            "backup_verified": "پشتیبان تأییدشده",
            "erasing": "در حال پاک کردن",
            "transferring": "در حال انتقال",
            "verifying": "در حال بازبینی",
            "completed": "کامل شد",
            "aborted": "متوقف پیش از پاک‌سازی (ECU دست‌نخورده)",
            "interrupted": "قطع‌شده — بازیابی لازم است",
            "recovering": "در حال بازیابی",
            "recovered": "بازیابی شد",
            "recovery_failed": "بازیابی ناموفق — دوباره تلاش کنید",
        },
        "bflash_result_ok": "✓ فلش کامل شد و با روتین بررسی و بازخوانی تأیید شد.",
        "bflash_result_recovered": "✓ پشتیبان تأییدشده بازگردانده و بازبینی شد.",
        "bflash_result_cancelled": (
            "فلش پیش از پاک‌سازی لغو شد؛ حافظه ECU شبیه‌سازی‌شده دست‌نخورده است."
        ),
        "bflash_result_aborted": (
            "فلش پیش از پاک‌سازی متوقف شد؛ حافظه ECU شبیه‌سازی‌شده دست‌نخورده است."
        ),
        "bflash_result_recovery_required": (
            "✗ فلش پس از شروع پاک‌سازی قطع شد. حافظه ممکن است ناقص باشد: بازیابی را اجرا کنید."
        ),
        "bflash_refused": "درخواست رد شد؛ چیزی ارسال یا تغییر داده نشد.",
        "bflash_discarded": "ژورنال کهنه بایگانی شد.",
        "bflash_error": "خطا در کار Bench.",
        "bflash_journal_title": "ژورنال تراکنش فلش",
        "bflash_journal_fields": {
            "status": "وضعیت",
            "detail": "توضیح",
            "path": "فایل ژورنال",
            "state": "حالت",
            "transaction": "تراکنش",
            "target": "هدف",
            "image": "تصویر",
            "backup": "پشتیبان",
            "backup_sha": "SHA-256 پشتیبان",
            "progress": "آخرین پیشرفت ذخیره‌شده",
            "reason": "علت توقف",
            "history": "تاریخچه",
        },
        "bflash_dialog_close": "بستن",
        "bflash_dialog_cancel": "انصراف",
        "bflash_modal_title": {
            "flash": "فعال‌سازی حالت فلش Bench (شبیه‌سازی)",
            "recovery": "بازیابی اضطراری (شبیه‌سازی)",
        },
        "bflash_modal_intro": {
            "flash": (
                "این تأیید فقط برای همین پنجره معتبر است و هیچ‌جا ذخیره نمی‌شود. جمله‌های زیر"
                " را نرم‌افزار نمی‌تواند بسنجد، پس شما تأییدشان می‌کنید؛ بررسی‌های ماشینی را"
                " خود نرم‌افزار انجام می‌دهد."
            ),
            "recovery": (
                "پشتیبان تأییدشده‌ای که در ژورنال ثبت شده بازگردانده می‌شود؛ پشتیبان تازه‌ای"
                " گرفته نمی‌شود. پیش از ادامه، جمله‌های زیر را دوباره تأیید کنید."
            ),
        },
        "bflash_statements_title": "تأیید انسانی",
        "bflash_statements": {
            "ecu_on_bench_out_of_vehicle": "ECU روی میز کار است و به هیچ خودرویی وصل نیست.",
            "supply_stable_and_current_limited": (
                "منبع تغذیه میز پایدار و دارای محدودیت جریان است."
            ),
            "wiring_verified": "سیم‌کشی تغذیه، زمین و CAN بررسی شده است.",
            "owner_approval_recorded": "صاحب پروژه این عملیات مشخص را تأیید کرده است.",
        },
        "bflash_checks_title": "بررسی‌های ماشینی",
        "bflash_checks": {
            "journal_clean": "هیچ ژورنالی مانع فلش جدید نیست",
            "journal_recoverable": "ژورنال ناتمام با پشتیبان ثبت‌شده",
            "backup_dir_writable": "پوشه پشتیبان قابل نوشتن است",
            "free_space": "فضای کافی برای پشتیبان",
            "image_valid": "تصویر برای این هدف پذیرفته شد",
            "checksums_valid": "چک‌سام‌ها معتبرند",
            "backup_verified": "پشتیبان روی دیسک دوباره تأیید شد",
            "target_simulated": "هدف ECU شبیه‌سازی‌شده است",
        },
        "bflash_check_override": "تأییدنشده؛ فقط با اجازه شبیه‌سازی",
        "bflash_operator": "نام اپراتور:",
        "bflash_phrase": "عبارت تأیید را دقیقاً تایپ کنید: {0}",
        "bflash_missing": "هنوز لازم است: {0}",
        "bflash_unmet": {
            "ecu_on_bench_out_of_vehicle": "ECU روی میز",
            "supply_stable_and_current_limited": "منبع تغذیه",
            "wiring_verified": "سیم‌کشی",
            "owner_approval_recorded": "تأیید صاحب پروژه",
            "operator": "نام اپراتور",
            "confirmation_phrase": "عبارت تأیید",
            "machine_checks": "بررسی‌های ماشینی",
        },
        "bflash_session_note": ("این حالت با بستن پنجره یا «خروج از حالت فلش Bench» پایان می‌یابد."),
        "bflash_confirm": {
            "flash": "تأیید و فعال‌سازی",
            "recovery": "تأیید و شروع بازیابی",
        },
        "bflash_rom_dialog": "انتخاب ROM برای فلش شبیه‌سازی‌شده",
        "bflash_maps_dialog": "تعریف نقشه‌ها با بلوک‌های چک‌سام (اختیاری)",
        "bflash_discard_title": "کنار گذاشتن ژورنال کهنه",
        "bflash_discard_text": (
            "این ژورنال پیش از هر پاک‌سازی متوقف شده است (ECU دست‌نخورده). آن را بایگانی کنیم"
            " تا فلش جدید ممکن شود؟"
        ),
        "bflash_banner_dirty": (
            "🚨 بازیابی اضطراری: یک فلش Bench شبیه‌سازی‌شده ناتمام مانده است ({0}). حافظه ECU"
            " ممکن است ناقص باشد و هر فلش جدیدی مسدود است. پوشه: {1}"
        ),
        "bflash_banner_corrupt": (
            "🚨 ژورنال فلش Bench خراب است و وضعیت ECU نامعلوم است. همه فلش‌ها مسدودند تا ژورنال"
            " دستی بررسی شود: {0}"
        ),
        "bflash_banner_recover": "شروع بازیابی",
        "bflash_banner_inspect": "بررسی ژورنال",
        "bench_open": "باز کردن ROM…",
        "bench_open_demo": "باز کردن نمونه شبیه‌سازی",
        "bench_load_defs": "بارگذاری تعریف نقشه‌ها…",
        "bench_undo": "بازگردانی",
        "bench_redo": "انجام دوباره",
        "bench_recalc": "بازمحاسبه چک‌سام‌ها",
        "bench_export": "خروجی ROM اصلاح‌شده…",
        "bench_open_dialog": "باز کردن فایل ROM (.bin)",
        "bench_open_filter": "ROM (*.bin);;All files (*)",
        "bench_defs_dialog": "باز کردن فایل تعریف نقشه‌ها",
        "bench_defs_filter": "JSON (*.json)",
        "bench_export_dialog": "ذخیره ROM اصلاح‌شده (فایلی دیگر، نه فایل اصلی)",
        "bench_export_filter": "ROM (*.bin)",
        "bench_no_rom": "یک فایل ROM یا نمونه شبیه‌سازی‌شده را باز کنید.",
        "bench_rom_line": "فایل: {0} — {1} بایت",
        "bench_simulated_badge": "⚠️ شبیه‌سازی‌شده — تصویر هیچ ECU واقعی نیست",
        "bench_defs_none": "تعریف نقشه‌ای بارگذاری نشده است؛ چک‌سام: پیکربندی نشده",
        "bench_defs_line": "تعریف‌ها: {0} ({1}) — اطمینان: {2} — {3} نقشه، {4} چک‌سام",
        "bench_defs_simulated": "شبیه‌سازی‌شده",
        "bench_defs_user": "ارائه‌شده توسط کاربر",
        "bench_warning_line": "⚠️ {0}",
        "bench_maps_title": "نقشه‌ها",
        "bench_categories": {
            "fuel": "سوخت",
            "ignition": "جرقه",
            "torque": "گشتاور",
            "limiter": "محدودکننده",
            "other": "سایر",
        },
        "bench_strings_title": "رشته‌های ASCII مشاهده‌شده (فقط شاهد، بدون شناسایی): {0}",
        "bench_strings_filter": "فیلتر رشته‌ها…",
        "bench_strings_columns": {"offset": "آفست", "text": "متن"},
        "bench_table_none": "یک نقشه را انتخاب کنید.",
        "bench_table_title": "{0} [{1}] — {2}×{3}",
        "bench_view_label": "نما:",
        "bench_view_modes": {
            "original": "اصلی",
            "modified": "اصلاح‌شده (قابل ویرایش)",
            "delta": "تفاوت",
            "delta_percent": "تفاوت ٪",
        },
        "bench_na": "نامشخص",
        "bench_cell_tip": "اصلی: {0} — فعلی: {1} — بازه مجاز: {2} تا {3} {4}",
        "bench_value_label": "مقدار:",
        "bench_set_btn": "اعمال روی خانه‌های انتخاب‌شده",
        "bench_percent_label": "درصد:",
        "bench_add_pct_btn": "افزودن درصد به انتخاب",
        "bench_checksums_title": (
            "چک‌سام‌ها («معتبر» یعنی مطابق الگوریتم تعریف‌شده؛ نه اینکه ECU آن را می‌پذیرد)"
        ),
        "bench_checksum_columns": {
            "id": "شناسه",
            "algorithm": "الگوریتم",
            "range": "بازه",
            "values": "ذخیره‌شده / محاسبه‌شده",
            "status": "وضعیت",
        },
        "bench_checksum_status": {
            "valid": "معتبر",
            "invalid": "نامعتبر",
            "not_configured": "پیکربندی نشده",
        },
        "bench_busy": "در حال کار…",
        "bench_failed": "انجام نشد: {0}",
        "bench_opened": "باز شد: {0}",
        "bench_defs_loaded": "تعریف نقشه‌ها بارگذاری شد.",
        "bench_undone": "بازگردانی شد: {0}",
        "bench_redone": "دوباره انجام شد: {0}",
        "bench_recalc_done": "{0} چک‌سام در نسخه کاری بازمحاسبه شد (فایل اصلی دست‌نخورده است).",
        "bench_edit_refused": "ویرایش رد شد (چیزی تغییر نکرد): {0}",
        "bench_edit_done": "{0} خانه تغییر کرد (بیشترین خطای گرد کردن: {1})",
        "bench_confirm_title": "خروجی ROM اصلاح‌شده",
        "bench_confirm_text": (
            "یک فایل جدید نوشته می‌شود؛ فایل اصلی هرگز تغییر نمی‌کند. این فایل برای هیچ خودرو یا"
            " ECU تأیید نشده است، چک‌سام فقط با الگوریتم تعریف‌شده بررسی شده و این برنامه هیچ"
            " راهی برای فلش کردن آن ندارد. یک فایل مانیفست کنار آن ذخیره می‌شود."
        ),
        "bench_confirm_check": (
            "می‌دانم این فایل برای هیچ ECU تأیید نشده و این برنامه آن را فلش نمی‌کند"
        ),
        "bench_export_cancelled": "خروجی لغو شد؛ چیزی نوشته نشد.",
        "bench_exported": "ذخیره شد: {0} (SHA-256 {1}…) — مانیفست: {2}",
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
        "vehicle_silent": (
            "خودرو به چند درخواست پیاپی هیچ پاسخی نداد؛ ارسال درخواست متوقف و اتصال قطع شد.\n"
            "سوئیچ بسته شده است؟ آداپتور را بررسی کنید و دوباره وصل شوید."
        ),
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
            "msg.vehicle_silent": "سوئیچ بسته شد؟ خودرو پاسخ نمی‌دهد؛ اتصال قطع شد: {0}",
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
            "msg.bench_journal_dirty": (
                "ژورنال فلش Bench ناتمام یا خراب است ({0})؛ بازیابی یا بررسی لازم است"
            ),
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
        "bench_title": "ROM workbench (offline file analysis)",
        "bench_warning": (
            "ROM workbench: offline file analysis only. Nothing is read from or written to any"
            " ECU from here, and an exported file is not approved for any vehicle or ECU. The"
            " bench flash below works against the simulated ECU only."
        ),
        "bflash_title": "Simulated bench flash (Phase 5)",
        "bflash_sim_badge": "SIMULATED — virtual ECU only",
        "bflash_intro": (
            "Runs the four-stage flash and its recovery against the virtual programmable ECU"
            " only. No real ECU, adapter or port can be reached, and there is no option to"
            " choose one."
        ),
        "bflash_mode_inactive": "Bench flash mode is off.",
        "bflash_mode_active": "Bench flash mode is on for this window (operator: {0}).",
        "bflash_enable": "Enable bench flash mode…",
        "bflash_disable": "Leave bench flash mode",
        "bflash_source_demo": "Image: the SIMULATED demo ROM (with its checksum definitions)",
        "bflash_source_file": "Image: {0} — definitions: {1}",
        "bflash_no_maps": "none",
        "bflash_choose_rom": "Choose ROM file…",
        "bflash_use_demo": "Use the demo ROM",
        "bflash_allow_unverified": (
            "Allow an unverified checksum (simulation only; recorded in the journal)"
        ),
        "bflash_fault_label": "Simulator fault (training):",
        "bflash_faults": {
            "none": "No fault",
            "power_loss": "Power loss at block 20",
            "drop": "Link drop at block 3",
            "nrc72": "Negative response 0x72 at block 2",
            "bad_key": "Wrong security key",
            "corrupt_readback": "Corrupted stage-4 read-back",
        },
        "bflash_start": "Start simulated flash",
        "bflash_cancel": "Cancel",
        "bflash_discard": "Discard stale journal…",
        "bflash_inspect": "Inspect journal",
        "bflash_do_not_disconnect": (
            "⚠️ The erase has started. Do not disconnect and do not close the program;"
            " cancelling is no longer possible. If the run is interrupted, the journal makes a"
            " recovery possible."
        ),
        "bflash_journal_label": "Journal:",
        "bflash_dir_label": "Bench directory:",
        "bflash_backup_label": "Backup file:",
        "bflash_sha_label": "Backup SHA-256:",
        "bflash_steps": {
            "pre_flight": "Pre-flight",
            "backup": "Mandatory full backup",
            "programming": "Programming transfer",
            "verify": "Verify",
        },
        "bflash_idle": "Ready.",
        "bflash_progress_bytes": "{0}: {1}/{2} bytes ({3}%)",
        "bflash_progress_block": "block {0}",
        "bflash_journal_unknown": "not checked yet",
        "bflash_journal_status": {
            "clean": "clean",
            "stale": "stale (ECU untouched)",
            "dirty": "unfinished — recovery required",
            "corrupt": "corrupt — inspect by hand",
        },
        "bflash_states": {
            "pending": "pending (ECU untouched)",
            "backup_verified": "backup verified",
            "erasing": "erasing",
            "transferring": "transferring",
            "verifying": "verifying",
            "completed": "completed",
            "aborted": "stopped before any erase (ECU untouched)",
            "interrupted": "interrupted — recovery required",
            "recovering": "recovering",
            "recovered": "recovered",
            "recovery_failed": "recovery failed — try again",
        },
        "bflash_result_ok": (
            "✓ Flash completed and verified by the check routine and the read-back."
        ),
        "bflash_result_recovered": "✓ The verified backup was restored and verified.",
        "bflash_result_cancelled": (
            "The flash was cancelled before any erase; the simulated ECU memory is untouched."
        ),
        "bflash_result_aborted": (
            "The flash stopped before any erase; the simulated ECU memory is untouched."
        ),
        "bflash_result_recovery_required": (
            "✗ The flash was interrupted after the erase started. The memory may be incomplete:"
            " run the recovery."
        ),
        "bflash_refused": "Refused; nothing was sent or changed.",
        "bflash_discarded": "The stale journal was archived.",
        "bflash_error": "Bench error.",
        "bflash_journal_title": "Flash transaction journal",
        "bflash_journal_fields": {
            "status": "Status",
            "detail": "Detail",
            "path": "Journal file",
            "state": "State",
            "transaction": "Transaction",
            "target": "Target",
            "image": "Image",
            "backup": "Backup",
            "backup_sha": "Backup SHA-256",
            "progress": "Last saved progress",
            "reason": "Stopped because",
            "history": "History",
        },
        "bflash_dialog_close": "Close",
        "bflash_dialog_cancel": "Cancel",
        "bflash_modal_title": {
            "flash": "Enable bench flash mode (simulation)",
            "recovery": "Emergency recovery (simulation)",
        },
        "bflash_modal_intro": {
            "flash": (
                "This confirmation holds for this window only and is never stored. The software"
                " cannot measure the statements below, so you confirm them; it verifies the"
                " machine checks itself."
            ),
            "recovery": (
                "The verified backup recorded in the journal is restored; no new backup is"
                " taken. Confirm the statements below again before continuing."
            ),
        },
        "bflash_statements_title": "Human attestation",
        "bflash_statements": {
            "ecu_on_bench_out_of_vehicle": (
                "The ECU is on the bench, not connected to any vehicle."
            ),
            "supply_stable_and_current_limited": (
                "The bench supply is stable and current limited."
            ),
            "wiring_verified": "Power, ground and CAN wiring were checked.",
            "owner_approval_recorded": "The project owner approved this specific operation.",
        },
        "bflash_checks_title": "Machine checks",
        "bflash_checks": {
            "journal_clean": "No journal blocks a new flash",
            "journal_recoverable": "Unfinished journal with a recorded backup",
            "backup_dir_writable": "The backup directory is writable",
            "free_space": "Enough free space for the backup",
            "image_valid": "The image is accepted for this target",
            "checksums_valid": "The checksums are valid",
            "backup_verified": "The backup verified again on disk",
            "target_simulated": "The target is the simulated ECU",
        },
        "bflash_check_override": "unverified; allowed by the simulation override only",
        "bflash_operator": "Operator name:",
        "bflash_phrase": "Type the confirmation phrase exactly: {0}",
        "bflash_missing": "Still needed: {0}",
        "bflash_unmet": {
            "ecu_on_bench_out_of_vehicle": "ECU on the bench",
            "supply_stable_and_current_limited": "bench supply",
            "wiring_verified": "wiring",
            "owner_approval_recorded": "owner approval",
            "operator": "operator name",
            "confirmation_phrase": "confirmation phrase",
            "machine_checks": "machine checks",
        },
        "bflash_session_note": (
            'The mode ends when this window closes or with "Leave bench flash mode".'
        ),
        "bflash_confirm": {
            "flash": "Confirm and enable",
            "recovery": "Confirm and start recovery",
        },
        "bflash_rom_dialog": "Choose a ROM for the simulated flash",
        "bflash_maps_dialog": "Map definitions with checksum blocks (optional)",
        "bflash_discard_title": "Discard the stale journal",
        "bflash_discard_text": (
            "This journal stopped before any erase (the ECU is untouched). Archive it so that a"
            " new flash can start?"
        ),
        "bflash_banner_dirty": (
            "🚨 Emergency recovery: a simulated bench flash did not finish ({0}). The ECU memory"
            " may be incomplete and every new flash is blocked. Directory: {1}"
        ),
        "bflash_banner_corrupt": (
            "🚨 The bench flash journal is corrupt and the ECU state is unknown. Every flash is"
            " blocked until the journal is inspected by hand: {0}"
        ),
        "bflash_banner_recover": "Start recovery",
        "bflash_banner_inspect": "Inspect journal",
        "bench_open": "Open ROM…",
        "bench_open_demo": "Open SIMULATED demo",
        "bench_load_defs": "Load map definitions…",
        "bench_undo": "Undo",
        "bench_redo": "Redo",
        "bench_recalc": "Recalculate checksums",
        "bench_export": "Export modified ROM…",
        "bench_open_dialog": "Open a ROM file (.bin)",
        "bench_open_filter": "ROM (*.bin);;All files (*)",
        "bench_defs_dialog": "Open a map definition file",
        "bench_defs_filter": "JSON (*.json)",
        "bench_export_dialog": "Save the modified ROM (a different file, never the source)",
        "bench_export_filter": "ROM (*.bin)",
        "bench_no_rom": "Open a ROM file or the SIMULATED demo.",
        "bench_rom_line": "File: {0} — {1} bytes",
        "bench_simulated_badge": "⚠️ SIMULATED — not an image of any real ECU",
        "bench_defs_none": "No map definitions loaded; checksums: not configured",
        "bench_defs_line": "Definitions: {0} ({1}) — confidence: {2} — {3} map(s), {4} checksum(s)",
        "bench_defs_simulated": "SIMULATED",
        "bench_defs_user": "user-supplied",
        "bench_warning_line": "⚠️ {0}",
        "bench_maps_title": "Maps",
        "bench_categories": {
            "fuel": "Fuel",
            "ignition": "Ignition",
            "torque": "Torque",
            "limiter": "Limiter",
            "other": "Other",
        },
        "bench_strings_title": "Observed ASCII strings (evidence only, no identification): {0}",
        "bench_strings_filter": "Filter strings…",
        "bench_strings_columns": {"offset": "Offset", "text": "Text"},
        "bench_table_none": "Select a map.",
        "bench_table_title": "{0} [{1}] — {2}×{3}",
        "bench_view_label": "View:",
        "bench_view_modes": {
            "original": "Original",
            "modified": "Modified (editable)",
            "delta": "Delta",
            "delta_percent": "Delta %",
        },
        "bench_na": "n/a",
        "bench_cell_tip": "Original: {0} — current: {1} — allowed: {2} to {3} {4}",
        "bench_value_label": "Value:",
        "bench_set_btn": "Set selected cells",
        "bench_percent_label": "Percent:",
        "bench_add_pct_btn": "Add % to selection",
        "bench_checksums_title": (
            "Checksums (valid = matches the configured algorithm; not proof any ECU accepts it)"
        ),
        "bench_checksum_columns": {
            "id": "Id",
            "algorithm": "Algorithm",
            "range": "Range",
            "values": "Stored / computed",
            "status": "Status",
        },
        "bench_checksum_status": {
            "valid": "Valid",
            "invalid": "Invalid",
            "not_configured": "Not configured",
        },
        "bench_busy": "Working…",
        "bench_failed": "Failed: {0}",
        "bench_opened": "Opened: {0}",
        "bench_defs_loaded": "Map definitions loaded.",
        "bench_undone": "Undone: {0}",
        "bench_redone": "Redone: {0}",
        "bench_recalc_done": (
            "{0} checksum(s) recalculated in the working copy (the source file is untouched)."
        ),
        "bench_edit_refused": "Edit refused (nothing changed): {0}",
        "bench_edit_done": "{0} cell(s) changed (largest rounding error: {1})",
        "bench_confirm_title": "Export modified ROM",
        "bench_confirm_text": (
            "A new file will be written; the source file is never changed. The file is not"
            " validated for any vehicle or ECU, its checksums were only checked against the"
            " configured algorithm, and this program has no way to flash it. A manifest is"
            " saved next to it."
        ),
        "bench_confirm_check": (
            "I understand this file is not validated for any ECU and this program cannot flash it"
        ),
        "bench_export_cancelled": "Export cancelled; nothing was written.",
        "bench_exported": "Saved: {0} (SHA-256 {1}…) — manifest: {2}",
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
        "vehicle_silent": (
            "The vehicle did not answer several polls in a row; polling stopped and the "
            "adapter was disconnected.\nIgnition turned off? Check the adapter and connect again."
        ),
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
            "msg.vehicle_silent": (
                "Ignition turned off? The vehicle stopped answering; disconnected: {0}"
            ),
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
            "msg.bench_journal_dirty": (
                "Bench flash journal is unfinished or corrupt ({0}): recover or inspect it"
            ),
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
