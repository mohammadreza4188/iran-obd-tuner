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
            "ident": "شناسایی خودرو",
            "bench": "تیونینگ (Bench)",
            "settings": "تنظیمات",
            "log": "رویدادها",
        },
        "port": "درگاه",
        "refresh_ports": "بازخوانی فهرست درگاه‌ها",
        "auto": "خودکار",
        "connect": "اتصال",
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
            "unsupported": "پشتیبانی نمی‌شود",
            "no_response": "بدون پاسخ",
            "malformed": "پاسخ نامعتبر",
            "no_data": "بدون داده",
        },
        "dtc_title": "کدهای خطای ذخیره‌شده",
        "dtc_note": "توضیح کدها از یک کاتالوگ عمومی و غیرمرجع (انگلیسی) می‌آید.",
        "no_dtc": "کد خطای ذخیره‌شده‌ای گزارش نشد.",
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
            "آداپتور واقعی ELM327 هنوز پیاده‌سازی نشده است (برنامه Milestone 2). برای کار "
            "بدون سخت‌افزار حالت شبیه‌سازی را انتخاب کنید."
        ),
        "adapter_unavailable": "آداپتور انتخاب‌شده در این نسخه در دسترس نیست:\n{0}",
        "connection_failed": "اتصال برقرار نشد:\n{0}",
        "recording_failed": "شروع ضبط ناموفق بود:\n{0}",
        "save_dialog_title": "ذخیره داده‌های زنده",
        "save_dialog_filter": "فایل CSV (*.csv)",
        "startup_failed": "راه‌اندازی برنامه ناموفق بود",
        "messages": {
            "msg.connected": "اتصال برقرار شد: {0} ({1})",
            "msg.disconnected": "اتصال قطع شد",
            "msg.connect_error": "خطای اتصال: {0}",
            "msg.adapter_unavailable": "آداپتور در دسترس نیست: {0}",
            "msg.connection_lost": "ارتباط با آداپتور قطع شد: {0}",
            "msg.dtc_read": "{0} کد خطا خوانده شد",
            "msg.dtc_read_error": "خطای خواندن کدهای خطا: {0}",
            "msg.clear_dtc_refused": "درخواست پاک کردن کدها توسط گارد ایمنی رد شد",
            "msg.ident_read": "شناسه خودرو خوانده شد: {0}",
            "msg.ident_error": "خطای خواندن شناسه خودرو: {0}",
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
            "ident": "Identification",
            "bench": "Bench / Tuning",
            "settings": "Settings",
            "log": "Events",
        },
        "port": "Port",
        "refresh_ports": "Refresh the port list",
        "auto": "Auto",
        "connect": "CONNECT",
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
            "unsupported": "Not supported",
            "no_response": "No response",
            "malformed": "Malformed response",
            "no_data": "No data",
        },
        "dtc_title": "Stored trouble codes",
        "dtc_note": "Descriptions come from a generic, non-authoritative catalogue (English).",
        "no_dtc": "No stored trouble codes were reported.",
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
            "The real ELM327 adapter is not implemented yet (planned for milestone 2). Use "
            "simulation mode to work without hardware."
        ),
        "adapter_unavailable": "The selected adapter is not available in this build:\n{0}",
        "connection_failed": "Connection failed:\n{0}",
        "recording_failed": "Recording could not be started:\n{0}",
        "save_dialog_title": "Save live data",
        "save_dialog_filter": "CSV files (*.csv)",
        "startup_failed": "Startup failed",
        "messages": {
            "msg.connected": "Connected: {0} ({1})",
            "msg.disconnected": "Disconnected",
            "msg.connect_error": "Connect error: {0}",
            "msg.adapter_unavailable": "Adapter not available: {0}",
            "msg.connection_lost": "Connection to the adapter lost: {0}",
            "msg.dtc_read": "{0} trouble code(s) read",
            "msg.dtc_read_error": "Trouble code read error: {0}",
            "msg.clear_dtc_refused": "Clear-codes request refused by the safety guard",
            "msg.ident_read": "Vehicle identification read: {0}",
            "msg.ident_error": "Vehicle identification error: {0}",
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
