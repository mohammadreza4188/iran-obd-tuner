"""Simple FA/EN strings for Phase 1 dashboard."""

from __future__ import annotations

from typing import Literal

Lang = Literal["fa", "en"]

COPY = {
    "fa": {
        "title": "داشبورد عیب‌یابی خودرو",
        "edition": "فاز ۱ — تشخیص زنده",
        "tabs": {
            "dashboard": "داشبورد",
            "graph": "نمودار زنده",
            "diag": "عیب‌یابی",
            "settings": "تنظیمات",
            "log": "گزارش",
        },
        "port": "درگاه",
        "refresh_ports": "بازخوانی درگاه‌ها",
        "auto": "خودکار",
        "connect": "اتصال",
        "disconnect": "قطع",
        "connecting": "در حال اتصال…",
        "connected": "متصل",
        "idle": "قطع",
        "sim": "شبیه‌سازی",
        "page": "صفحه",
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
        "dtc_title": "کدهای خطا",
        "no_dtc": "کد خطایی نیست.",
        "clear_dtc": "پاک کردن کدها",
        "clear_blocked": (
            "پاک کردن کدهای خطا در این نسخه غیرفعال است (فقط‌خواندنی) و نیازمند تأیید تیم است."
        ),
        "refresh_dtc": "خواندن مجدد",
        "settings_lang": "زبان",
        "settings_mode": "حالت اتصال",
        "mode_real": "آداپتور واقعی",
        "mode_sim": "شبیه‌سازی (بدون سخت‌افزار)",
        "sim_note": "اگر آداپتور OBD وصل نیست، حالت شبیه‌سازی را انتخاب کنید.",
        "log_empty": "هنوز رویدادی ثبت نشده.",
    },
    "en": {
        "title": "Vehicle Diagnostics Dashboard",
        "edition": "Phase 1 — live diagnostics",
        "tabs": {
            "dashboard": "Dashboard",
            "graph": "Live Graph",
            "diag": "Diagnostics",
            "settings": "Settings",
            "log": "Log",
        },
        "port": "Port",
        "refresh_ports": "Refresh ports",
        "auto": "Auto",
        "connect": "CONNECT",
        "disconnect": "DISCONNECT",
        "connecting": "Connecting…",
        "connected": "Connected",
        "idle": "Offline",
        "sim": "Simulation",
        "page": "Page",
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
        "dtc_title": "Trouble codes",
        "no_dtc": "No trouble codes.",
        "clear_dtc": "Clear codes",
        "clear_blocked": (
            "Clearing trouble codes is disabled in this build (read-only) and needs team approval."
        ),
        "refresh_dtc": "Refresh",
        "settings_lang": "Language",
        "settings_mode": "Connection mode",
        "mode_real": "Real adapter",
        "mode_sim": "Simulation (no hardware)",
        "sim_note": "If no OBD adapter is plugged in, use simulation mode.",
        "log_empty": "No events yet.",
    },
}

SAMPLE_DTC = [
    {"code": "P0300", "fa": "احتراق ناقص تصادفی", "en": "Random misfire"},
    {"code": "P0171", "fa": "مخلوط سوخت رقیق (بانک ۱)", "en": "System too lean bank 1"},
    {"code": "P0420", "fa": "بازده کاتالیست زیر حد", "en": "Catalyst efficiency below threshold"},
]
