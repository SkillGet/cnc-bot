import asyncio
import logging
import math
import os
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cnc_bot")

CALCULATE_BUTTON = "📊 Расчёт режимов"
THREAD_BUTTON = "🔩 Резьба"
ALARMS_BUTTON = "🚨 Алармы"
MOTOR_EFFICIENCY = 0.8
REFERENCE_TOOL_LIFE_MIN = 60.0
TAYLOR_EXPONENT = {"carbide": 0.22, "hss": 0.12, "cbn": 0.25}
ISO_CLASSES = ("P", "M", "K", "N", "S", "H")
TOOL_LABELS = {
    "carbide": "Твердосплав",
    "hss": "HSS",
    "cbn": "CBN",
}
MODE_LABELS = {
    "rough": "Черновая",
    "finish": "Чистовая",
}
TOOL_FACTORS = {
    "carbide": {"feed": 1.0},
    "hss": {"feed": 0.75},
    "cbn": {"feed": 0.85},
}
TOOL_VC_FACTORS = {
    "carbide": {"P": 1.0, "M": 1.0, "K": 1.0, "N": 1.0, "S": 1.0, "H": 1.0},
    "hss": {"P": 0.30, "M": 0.25, "K": 0.35, "N": 0.50, "S": 0.20, "H": 0.15},
    "cbn": {"P": 1.50, "M": 1.50, "K": 1.20, "N": 1.00, "S": 1.50, "H": 2.50},
}
TOOL_PARAMS = {
    tool: {
        "k": TOOL_VC_FACTORS[tool],
        "m": TAYLOR_EXPONENT[tool],
        "T_ref": 30.0 if tool == "hss" else REFERENCE_TOOL_LIFE_MIN,
    }
    for tool in TOOL_LABELS
}
TARGET_TOOL_LIFE_MIN = 90.0
MODE_FACTORS = {
    "rough": {"feed": 1.0},
    "finish": {"feed": 0.4},
}
MACHINES: dict[str, dict[str, Any]] = {
    "small": {
        "name": "Малый (5.5 кВт, 3000 об/мин)",
        "max_rpm": 3000,
        "max_power": 5.5,
    },
    "medium": {
        "name": "Средний (11 кВт, 4000 об/мин)",
        "max_rpm": 4000,
        "max_power": 11.0,
    },
    "big": {
        "name": "Большой (22 кВт, 2500 об/мин)",
        "max_rpm": 2500,
        "max_power": 22.0,
    },
    "heavy": {
        "name": "Тяжёлый (37 кВт, 1500 об/мин)",
        "max_rpm": 1500,
        "max_power": 37.0,
    },
}

ALARMS: dict[str, dict[str, str]] = {
    "fanuc": {
        "OT001": "Перебег по X. Проверь лимиты, выведи рукояткой, обнули.",
        "OT002": "Перебег по Z. То же.",
        "OT010": "Софт-лимит X. Отведи в обратную сторону.",
        "OT011": "Софт-лимит Z.",
        "SV0401": "VRDY OFF — привод не готов. Проверь питание, MCC, E-stop.",
        "SV0410": "Перегрузка оси. Проверь механику, тормоз, инерцию.",
        "SV0414": "Ошибка по току/перегрузке. Проверь двигатель и кабель.",
        "SV0430": "Перегрев двигателя оси. Дай остыть, проверь вентиляцию.",
        "SV0433": "Низкое напряжение DC-link. Проверь питание, контактор.",
        "SV0436": "Перегрев привода. Проверь обдув шкафа.",
        "SV0600": "Ошибка связи серво-усилителя с CNC.",
        "SV0602": "Ошибка связи. Проверь кабель FSSB.",
        "SP0741": "Перегрев шпинделя. Дай остыть.",
        "SP9001": "Ошибка шпинделя: нет вращения. Проверь датчик, ремень, привод.",
        "SP9012": "Перегрев шпинделя. Дай остыть, проверь СОЖ/масло.",
        "SP9015": "Ошибка шпиндельного усилителя.",
        "PS0001": "Отключение питания. Проверь вводной автомат.",
        "EX1000": "Аварийный стоп (E-stop). Отпусти грибок, сбрось RESET.",
        "EX1010": "Не закрыта дверь. Закрой.",
        "EX1020": "Сработал датчик патрона.",
        "EX1040": "Ошибка смены инструмента.",
    },
    "siemens": {
        "3000": "Перебег по оси. Отведи, сбрось.",
        "20001": "Нет разрешения регулятора. Проверь E-stop, двери.",
        "20006": "Низкое напряжение DC-link.",
        "20010": "Перегрев силового модуля.",
        "20011": "Перегрев двигателя.",
        "20012": "Перегрузка по току.",
        "20013": "Ошибка энкодера.",
        "20020": "Ошибка геометрии инструмента. Проверь корректора.",
        "20060": "Ошибка связи с приводом.",
        "20063": "Ошибка связи с приводом. Проверь PROFIBUS/DRIVE-CLiQ.",
        "30050": "Ошибка привода по току. Проверь двигатель.",
        "300501": "Перегрев силового модуля. Проверь обдув.",
        "700000": "Общий сбой. Смотри журнал ошибок.",
        "701000": "Ошибка шпинделя. Проверь датчик, ремень.",
        "710000": "Ошибка смены инструмента.",
        "730000": "Ошибка гидравлики.",
        "740000": "Ошибка пневматики.",
    },
    "haas": {
        "100": "Перебег по X. Отведи, RESET.",
        "101": "Перебег по Z.",
        "108": "Дверь открыта. Закрой.",
        "110": "Нет воздуха. Проверь пневматику.",
        "112": "Ошибка смазки. Проверь масло.",
        "114": "Перегрев шпинделя.",
        "118": "Перегрузка серво.",
        "120": "Ошибка энкодера.",
        "125": "Ошибка батареи энкодера.",
        "127": "Аварийный стоп.",
        "133": "Нет связи с серво.",
        "136": "Ошибка револьвера.",
        "138": "Ошибка смены инструмента.",
        "144": "Ошибка гидравлики.",
        "162": "Ошибка корректора.",
        "166": "Ошибка привязки.",
        "175": "Перегрев привода.",
        "185": "Ошибка энкодера шпинделя.",
        "222": "Ошибка шпиндельного энкодера. Проверь кабель и батарею.",
    },
}

# Стартовые справочные данные для точения. kc задаётся в Н/мм².
# Vc, f, ap и коэффициенты инструмента/обработки являются оценочными.
MATERIALS: dict[str, dict[str, Any]] = {
    "st20": {"name": "Сталь 20", "iso": "P", "hb": 156, "kc": 1600, "vc": 180, "f": 0.45},
    "st35": {"name": "Сталь 35", "iso": "P", "hb": 179, "kc": 1800, "vc": 170, "f": 0.45},
    "st45": {"name": "Сталь 45", "iso": "P", "hb": 200, "kc": 1950, "vc": 160, "f": 0.45},
    "st50": {"name": "Сталь 50", "iso": "P", "hb": 217, "kc": 2050, "vc": 150, "f": 0.40},
    "st40x": {"name": "Сталь 40Х", "iso": "P", "hb": 230, "kc": 2100, "vc": 150, "f": 0.40},
    "30hgsa": {"name": "30ХГСА", "iso": "P", "hb": 250, "kc": 2200, "vc": 140, "f": 0.35},
    "st20x": {"name": "Сталь 20Х", "iso": "P", "hb": 180, "kc": 1800, "vc": 170, "f": 0.45},
    "20hnma": {"name": "20ХНМА", "iso": "P", "hb": 240, "kc": 2150, "vc": 140, "f": 0.35},
    "a12": {"name": "А12 автоматная", "iso": "P", "hb": 170, "kc": 1450, "vc": 200, "f": 0.50},
    "65g": {"name": "Сталь 65Г", "iso": "P", "hb": 230, "kc": 2150, "vc": 140, "f": 0.35},
    "15x": {"name": "Сталь 15Х", "iso": "P", "hb": 190, "kc": 1850, "vc": 170, "f": 0.45},
    "12x18": {"name": "12Х18Н10Т", "iso": "M", "hb": 180, "kc": 2200, "vc": 90, "f": 0.30},
    "08x18": {"name": "08Х18Н10Т", "iso": "M", "hb": 170, "kc": 2150, "vc": 95, "f": 0.30},
    "20x13": {"name": "20Х13", "iso": "M", "hb": 200, "kc": 2300, "vc": 100, "f": 0.30},
    "sch15": {"name": "СЧ15", "iso": "K", "hb": 160, "kc": 1100, "vc": 140, "f": 0.50},
    "sch20": {"name": "СЧ20", "iso": "K", "hb": 180, "kc": 1250, "vc": 120, "f": 0.45},
    "sch30": {"name": "СЧ30", "iso": "K", "hb": 210, "kc": 1400, "vc": 100, "f": 0.40},
    "vch45": {"name": "ВЧ45", "iso": "K", "hb": 220, "kc": 1500, "vc": 90, "f": 0.35},
    "vch50": {"name": "ВЧ50", "iso": "K", "hb": 240, "kc": 1650, "vc": 80, "f": 0.35},
    "kch35": {"name": "КЧ35-10", "iso": "K", "hb": 150, "kc": 1080, "vc": 126, "f": 0.40},
    "d16": {"name": "Д16", "iso": "N", "hb": 95, "kc": 700, "vc": 350, "f": 0.50},
    "amg": {"name": "АМг", "iso": "N", "hb": 70, "kc": 600, "vc": 400, "f": 0.55},
    "amc": {"name": "АМц", "iso": "N", "hb": 60, "kc": 550, "vc": 450, "f": 0.60},
    "l63": {"name": "Латунь Л63", "iso": "N", "hb": 80, "kc": 750, "vc": 250, "f": 0.50},
    "ls59": {"name": "ЛС59-1", "iso": "N", "hb": 90, "kc": 650, "vc": 300, "f": 0.55},
    "bra9": {"name": "БрА9Ж3Л", "iso": "N", "hb": 170, "kc": 1100, "vc": 180, "f": 0.40},
    "m1": {"name": "Медь М1", "iso": "N", "hb": 50, "kc": 850, "vc": 180, "f": 0.45},
    "vt1": {"name": "Титан ВТ1-0", "iso": "S", "hb": 250, "kc": 2100, "vc": 60, "f": 0.25},
    "vt6": {"name": "Титан ВТ6", "iso": "S", "hb": 320, "kc": 2600, "vc": 40, "f": 0.25},
    "st45z": {"name": "Сталь 45 закалённая", "iso": "H", "hb": 480, "kc": 3800, "vc": 50, "f": 0.15},
}
MATERIALS_PER_PAGE = 8


def material_page_count() -> int:
    return math.ceil(len(MATERIALS) / MATERIALS_PER_PAGE)

router = Router()


class TurningFlow(StatesGroup):
    choosing_material = State()
    choosing_tool = State()
    choosing_mode = State()
    choosing_machine = State()
    entering_diameter = State()


class GCodeFlow(StatesGroup):
    result = State()
    entering_stock_diameter = State()
    entering_length = State()


class ThreadFlow(StatesGroup):
    choosing_type = State()
    entering_metric_major = State()
    entering_metric_pitch = State()
    entering_metric_length = State()
    entering_inch_diameter = State()
    entering_inch_tpi = State()
    entering_inch_length = State()
    entering_pipe_size = State()
    entering_pipe_length = State()
    choosing_side = State()


class AlarmFlow(StatesGroup):
    choosing_controller = State()
    entering_query = State()


def cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="Отмена", callback_data="cancel")]


def material_page_text(page: int) -> str:
    return (
        f"<b>Шаг 1 из 4 · материал. Страница "
        f"{page + 1}/{material_page_count()}</b>\n"
        "Выберите материал заготовки:"
    )


def material_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    materials = list(MATERIALS.items())
    start = page * MATERIALS_PER_PAGE
    buttons = [
        InlineKeyboardButton(
            text=material["name"],
            callback_data=f"material:{key}",
        )
        for key, material in materials[start : start + MATERIALS_PER_PAGE]
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(text="◀ Назад", callback_data="material_page:prev"),
            InlineKeyboardButton(text="Далее ▶", callback_data="material_page:next"),
        ]
    )
    rows.append(cancel_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tool_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TOOL_LABELS["carbide"],
                    callback_data="tool:carbide",
                ),
                InlineKeyboardButton(
                    text=TOOL_LABELS["hss"],
                    callback_data="tool:hss",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=TOOL_LABELS["cbn"],
                    callback_data="tool:cbn",
                )
            ],
            cancel_row(),
        ]
    )


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=MODE_LABELS["rough"],
                    callback_data="mode:rough",
                ),
                InlineKeyboardButton(
                    text=MODE_LABELS["finish"],
                    callback_data="mode:finish",
                ),
            ],
            cancel_row(),
        ]
    )


def machine_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=machine["name"],
            callback_data=f"machine:{key}",
        )
        for key, machine in MACHINES.items()
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(cancel_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 G-код (Fanuc)",
                    callback_data="gcode:start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Новый расчёт",
                    callback_data="new_calculation",
                )
            ]
        ]
    )


def gcode_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Новый расчёт",
                    callback_data="new_calculation",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 G-код ещё раз",
                    callback_data="gcode:again",
                )
            ],
        ]
    )


def thread_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Новый расчёт",
                    callback_data="new_calculation",
                )
            ]
        ]
    )


def thread_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📐 Метрическая (M)",
                    callback_data="thread_type:metric",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📏 Дюймовая (UNC/UNF)",
                    callback_data="thread_type:inch",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔧 Трубная (G)",
                    callback_data="thread_type:pipe",
                )
            ],
            cancel_row(),
        ]
    )


def thread_side_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Наружная",
                    callback_data="thread_side:external",
                ),
                InlineKeyboardButton(
                    text="Внутренняя",
                    callback_data="thread_side:internal",
                ),
            ],
            cancel_row(),
        ]
    )


def alarm_controller_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Fanuc",
                    callback_data="alarm_controller:fanuc",
                ),
                InlineKeyboardButton(
                    text="Siemens",
                    callback_data="alarm_controller:siemens",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Haas",
                    callback_data="alarm_controller:haas",
                )
            ],
            cancel_row(),
        ]
    )


def alarm_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Другая ошибка",
                    callback_data="alarm:again",
                )
            ]
        ]
    )


def parse_diameter(text: str) -> float | None:
    try:
        diameter = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(diameter) or diameter <= 0:
        return None
    return diameter


def find_alarm_matches(
    controller: str,
    query: str,
) -> list[tuple[str, str]]:
    alarms = ALARMS[controller]
    normalized_query = query.strip().casefold()
    if not normalized_query or normalized_query == "enter":
        return list(alarms.items())

    for code, description in alarms.items():
        if normalized_query == code.casefold():
            return [(code, description)]

    return [
        (code, description)
        for code, description in alarms.items()
        if normalized_query in code.casefold()
        or normalized_query in description.casefold()
    ]


def format_alarm_results(
    controller: str,
    query: str,
    matches: list[tuple[str, str]],
) -> str:
    controller_name = controller.capitalize()
    normalized_query = query.strip().casefold()
    if not matches:
        return f"По '{escape(query.strip())}' ничего не найдено"

    title = (
        f"<b>Все аварийные сообщения · {controller_name}</b>"
        if not normalized_query or normalized_query == "enter"
        else f"<b>Алармы · {controller_name}</b>"
    )
    lines = [title, ""]
    lines.extend(
        f"<code>{escape(code)}</code> — {escape(description)}"
        for code, description in matches
    )
    return "\n".join(lines)


def recommend_values(
    material_key: str,
    tool_key: str,
    mode_key: str,
) -> tuple[float, float, float, float, float]:
    material = MATERIALS[material_key]
    iso_class = material["iso"]
    is_rough = mode_key == "rough"

    tool_params = TOOL_PARAMS[tool_key]
    k = tool_params["k"][iso_class]
    vc_start = material["vc"] * k
    if not is_rough and not (tool_key == "hss" and iso_class == "H"):
        vc_start *= 1.25

    m = tool_params["m"]
    t_ref = tool_params["T_ref"]
    t_target = TARGET_TOOL_LIFE_MIN
    taylor_c = vc_start * (t_ref**m)
    vc_opt = taylor_c / (t_target**m)
    vc = max(vc_start * 0.35, min(vc_opt, vc_start * 1.6))

    if is_rough:
        ap = 2.0 if iso_class in ("S", "H") else 3.5
    else:
        ap = 0.5

    return (
        vc,
        material["f"]
        * TOOL_FACTORS[tool_key]["feed"]
        * MODE_FACTORS[mode_key]["feed"],
        ap,
        taylor_c,
        m,
    )


def calculate_turning(
    material_key: str,
    tool_key: str,
    mode_key: str,
    diameter_mm: float,
) -> dict[str, float]:
    material = MATERIALS[material_key]
    vc, feed, ap, taylor_c, exponent = recommend_values(
        material_key,
        tool_key,
        mode_key,
    )

    # n = 1000*Vc/(pi*d), Vf = n*f; расчёт ведётся по неокруглённому n.
    rpm = 1000.0 * vc / (math.pi * diameter_mm)
    feed_rate = rpm * feed

    # Pz = kc*ap*f; kc измеряется в Н/мм².
    cutting_force = material["kc"] * ap * feed
    cutting_power_kw = cutting_force * vc / 60_000.0
    motor_power_kw = cutting_power_kw / MOTOR_EFFICIENCY
    torque_nm = 9550.0 * motor_power_kw / rpm

    tool_life_min = (taylor_c / vc) ** (1.0 / exponent)

    is_rough = mode_key == "rough"
    ra_theoretical = (feed**2 / (32.0 * 1.0)) * 1000.0
    ra_coefficient = 3.0 if is_rough else 1.8
    roughness_um = ra_theoretical * ra_coefficient

    return {
        "vc": vc,
        "rpm": rpm,
        "feed": feed,
        "ap": ap,
        "vf": feed_rate,
        "pz": cutting_force,
        "cutting_power": cutting_power_kw,
        "motor_power": motor_power_kw,
        "torque": torque_nm,
        "life": tool_life_min,
        "ra": roughness_um,
        "ra_coefficient": ra_coefficient,
    }


def fmt(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def fmt_gcode(value: float, digits: int = 3) -> str:
    formatted = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return formatted or "0"


def calculate_thread_values(
    major_mm: float,
    pitch_mm: float,
    side: str,
) -> dict[str, float]:
    is_external = side == "external"
    height_factor = 0.6134 if is_external else 0.5413
    height_mm = height_factor * pitch_mm
    target_x_mm = major_mm - 2.0 * height_mm if is_external else major_mm
    cutting_speed = 60.0 if major_mm > 8.0 else 80.0
    rpm = 1000.0 * cutting_speed / (math.pi * major_mm)
    return {
        "h": height_mm,
        "target_x": target_x_mm,
        "vc": cutting_speed,
        "rpm": rpm,
    }


def format_thread_gcode(
    thread_kind: str,
    major_mm: float,
    pitch_mm: float,
    length_mm: float,
    side: str,
    values: dict[str, float],
    nominal_label: str | None = None,
) -> str:
    side_name = "наружная" if side == "external" else "внутренняя"
    major = fmt_gcode(major_mm, 3)
    pitch = fmt_gcode(pitch_mm, 4)
    target_x = fmt_gcode(values["target_x"], 3)
    length = fmt_gcode(length_mm, 3)
    spindle_rpm = int(round(values["rpm"]))
    g50_rpm = int(min(values["rpm"], 3000.0))
    if side == "external":
        approach_x_mm = major_mm + 4.0
    else:
        minor_mm = major_mm - 2.0 * values["h"]
        approach_x_mm = max(minor_mm - 2.0, 1.0)
    approach_x = fmt_gcode(approach_x_mm, 3)
    height_p = int(values["h"] * 1000.0)
    height_q = int(values["h"] * 300.0)

    if thread_kind == "metric":
        title = f"M{pitch}"
        description = f"M{major}x{pitch} {side_name}"
    elif thread_kind == "inch":
        title = f"UNC/UNF {pitch}"
        description = f"UNC/UNF {major}x{pitch} {side_name}"
    else:
        pipe_label = nominal_label or major
        title = f"G {pipe_label}"
        description = f"G{pipe_label} {side_name}, {pitch} mm"

    return "\n".join(
        [
            f"(FANUC REZBA {title})",
            f"({description})",
            f"(Vc={fmt_gcode(values['vc'], 1)} m/min, n={spindle_rpm} rpm, "
            f"h={fmt_gcode(values['h'], 3)} mm, L={length} mm)",
            "",
            "G21 G40 G99 G80",
            f"G50 S{g50_rpm}",
            f"G97 S{spindle_rpm} M03",
            f"G00 X{approach_x} Z5.0",
            "",
            f"G76 P020060 Q{int(pitch_mm * 100.0)} R{int(pitch_mm * 50.0)}",
            f"G76 X{target_x} Z-{length} P{height_p} Q{height_q} F{pitch}",
            "",
            "G00 X200.0 Z200.0",
            "M05",
            "M30",
        ]
    )


def format_fanuc_gcode(
    material_key: str,
    mode_key: str,
    stock_diameter_mm: float,
    target_diameter_mm: float,
    length_mm: float,
    values: dict[str, float],
) -> str:
    material = MATERIALS[material_key]
    is_rough = mode_key == "rough"
    stock_diameter = fmt_gcode(stock_diameter_mm, 3)
    target_diameter = fmt_gcode(target_diameter_mm, 3)
    if "." not in target_diameter:
        target_diameter += ".0"
    retract_diameter = fmt_gcode(stock_diameter_mm + 2.0, 3)
    length = fmt_gcode(length_mm, 3)
    vc = int(values["vc"])
    rpm_limit = int(min(values["rpm"], 4000.0))
    feed = fmt_gcode(values["feed"], 3)
    ap = fmt_gcode(values["ap"], 3)

    lines = [
        "(FANUC TOCHENIE)",
        f"(Material: {material['name']} HB{material['hb']})",
        f"(Vc={fmt_gcode(values['vc'], 2)} m/min, f={feed} mm/rev, ap={ap} mm)",
        "",
        "G21 G40 G99 G80",
        f"G50 S{rpm_limit}",
        f"G96 S{vc} M03",
        f"G00 X{stock_diameter} Z2.0",
        "",
    ]

    if is_rough:
        lines.extend(
            [
                f"G71 U{ap} R0.5",
                f"G71 P10 Q20 U0.3 W0.1 F{feed}",
                f"N10 G00 X{target_diameter}",
                f"G01 Z-{length}",
                f"N20 G01 X{retract_diameter}",
                "G70 P10 Q20 F0.1",
            ]
        )
    else:
        lines.extend(
            [
                f"G01 X{target_diameter} F{feed}",
                f"G01 Z-{length}",
            ]
        )

    lines.extend(
        [
            "",
            "G00 X200.0 Z200.0",
            "G97 S500",
            "M05",
            "M30",
        ]
    )
    return "\n".join(lines)


def tool_warning(material_key: str, tool_key: str) -> str:
    if tool_key != "cbn":
        return ""
    if MATERIALS[material_key]["hb"] >= 400:
        return ""
    return (
        "\n<b>Проверьте инструмент:</b> CBN обычно применяют для твёрдых "
        "материалов. Уточните совместимость пластины с заготовкой."
    )


def machine_warnings(machine_key: str, values: dict[str, float]) -> str:
    machine = MACHINES[machine_key]
    warnings = []
    if values["rpm"] > machine["max_rpm"]:
        warnings.append("🛑 Обороты выше предела станка.")
    if values["motor_power"] > machine["max_power"]:
        warnings.append("🛑 Мощность выше привода.")
    if not warnings:
        return ""
    return "\n".join(warnings)


def calculation_warnings(
    material_key: str,
    tool_key: str,
    mode_key: str,
    machine_key: str,
    values: dict[str, float],
) -> str:
    material = MATERIALS[material_key]
    warnings = []
    is_rough = mode_key == "rough"

    if material["iso"] == "H" and tool_key == "hss":
        warnings.append("⚠️ HSS не режет закалённую сталь.")
    if values["rpm"] < 50:
        warnings.append("⚠️ Обороты слишком низкие.")
    if values["motor_power"] < 0.1 and is_rough:
        warnings.append("⚠️ Мощность мала для черновой.")

    limit_warnings = machine_warnings(machine_key, values)
    if limit_warnings:
        warnings.extend(limit_warnings.splitlines())

    if not warnings:
        return ""
    return "\n\n<b>Предупреждения</b>\n" + "\n".join(warnings)


def format_result(
    material_key: str,
    tool_key: str,
    mode_key: str,
    machine_key: str,
    diameter_mm: float,
    values: dict[str, float],
) -> str:
    material_name = escape(MATERIALS[material_key]["name"])
    tool_name = escape(TOOL_LABELS[tool_key])
    mode_name = escape(MODE_LABELS[mode_key].lower())
    machine_name = escape(MACHINES[machine_key]["name"])
    return (
        "<b>Расчёт режимов · точение</b>\n"
        f"Материал: <b>{material_name}</b>\n"
        f"Станок: <b>{machine_name}</b>\n"
        f"Инструмент: <b>{tool_name}</b> · {mode_name} · "
        f"d = {fmt(diameter_mm)} мм\n\n"
        "<b>Рекомендуемые параметры</b>\n"
        f"Vc — скорость резания: <b>{fmt(values['vc'], 1)} м/мин</b>\n"
        f"n — частота вращения: <b>{fmt(values['rpm'], 0)} об/мин</b>\n"
        f"f — подача: <b>{fmt(values['feed'], 3)} мм/об</b>\n"
        f"ap — глубина резания: <b>{fmt(values['ap'], 2)} мм</b>\n"
        f"Vf — минутная подача: <b>{fmt(values['vf'], 1)} мм/мин</b>\n\n"
        "<b>Нагрузка и качество</b>\n"
        f"Pz — сила резания: <b>{fmt(values['pz'], 1)} Н</b>\n"
        f"Мощность двигателя: <b>{fmt(values['motor_power'], 3)} кВт</b>\n"
        f"Момент: <b>{fmt(values['torque'], 2)} Н·м</b>\n"
        f"Стойкость по Тейлору: <b>≈ {fmt(values['life'], 1)} мин</b>\n"
        f"Ra: <b>≈ {fmt(values['ra'], 2)} мкм</b> "
        f"(коэффициент {fmt(values['ra_coefficient'], 2)})"
        f"{tool_warning(material_key, tool_key)}"
        f"{calculation_warnings(material_key, tool_key, mode_key, machine_key, values)}\n\n"
        "<i>Ориентировочная оценка, а не гарантия режима. Vc, f, ap, kc и "
        "коэффициенты приняты по стартовой таблице; константа Тейлора "
        "калибрована условно. Проверьте данные производителя инструмента, "
        "ограничения станка, закрепление и вылет. Начинайте с осторожной "
        "нагрузки и корректируйте режим по стружке, звуку и вибрации.</i>"
    )


async def start_calculation(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TurningFlow.choosing_material)
    await state.update_data(material_page=0)
    await message.answer(
        material_page_text(0),
        reply_markup=material_keyboard(0),
    )


async def start_thread_calculation(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ThreadFlow.choosing_type)
    await message.answer(
        "<b>Расчёт резьбы</b>\nВыберите тип резьбы:",
        reply_markup=thread_type_keyboard(),
    )


async def start_alarm_lookup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AlarmFlow.choosing_controller)
    await message.answer(
        "<b>Поиск алармов</b>\nВыберите стойку:",
        reply_markup=alarm_controller_keyboard(),
    )


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>CNC — расчёт режимов резания</b>\n\n"
        "Ориентировочный расчёт токарной обработки: обороты, подача, "
        "нагрузка шпинделя, мощность и оценка шероховатости.\n\n"
        "Нажмите кнопку, чтобы начать.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=CALCULATE_BUTTON)],
                [KeyboardButton(text=THREAD_BUTTON)],
                [KeyboardButton(text=ALARMS_BUTTON)],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Command("cancel"))
async def command_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущий расчёт отменён.")


@router.message(F.text == CALCULATE_BUTTON)
async def calculate_button(message: Message, state: FSMContext) -> None:
    await start_calculation(message, state)


@router.message(F.text == THREAD_BUTTON)
async def thread_button(message: Message, state: FSMContext) -> None:
    await start_thread_calculation(message, state)


@router.message(F.text == ALARMS_BUTTON)
async def alarms_button(message: Message, state: FSMContext) -> None:
    await start_alarm_lookup(message, state)


@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Расчёт отменён")
    if callback.message:
        await callback.message.answer("Текущий расчёт отменён.")


@router.callback_query(
    AlarmFlow.choosing_controller,
    F.data.startswith("alarm_controller:"),
)
async def select_alarm_controller(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    controller = callback.data.split(":", maxsplit=1)[1]
    if controller not in ALARMS:
        await callback.answer("Неизвестная стойка", show_alert=True)
        return

    await state.update_data(alarm_controller=controller)
    await state.set_state(AlarmFlow.entering_query)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите номер или часть текста ошибки. "
            "Enter — список всех."
        )


@router.message(AlarmFlow.entering_query)
async def enter_alarm_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    controller = data.get("alarm_controller")
    if controller not in ALARMS:
        await state.clear()
        await message.answer(
            "Состояние поиска сбросилось. Нажмите «🚨 Алармы», "
            "чтобы начать заново."
        )
        return

    query = message.text or ""
    matches = find_alarm_matches(controller, query)
    await state.clear()
    await message.answer(
        format_alarm_results(controller, query, matches),
        reply_markup=alarm_result_keyboard(),
    )


@router.callback_query(F.data == "alarm:again")
async def alarm_again(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await start_alarm_lookup(callback.message, state)


@router.callback_query(
    ThreadFlow.choosing_type,
    F.data.startswith("thread_type:"),
)
async def select_thread_type(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    thread_type = callback.data.split(":", maxsplit=1)[1]
    if thread_type not in {"metric", "inch", "pipe"}:
        await callback.answer("Неизвестный тип резьбы", show_alert=True)
        return

    await state.update_data(thread_type=thread_type)
    await callback.answer()
    if not callback.message:
        return

    if thread_type == "metric":
        await state.set_state(ThreadFlow.entering_metric_major)
        await callback.message.answer(
            "<b>Метрическая резьба · диаметр</b>\n"
            "Введите номинальный диаметр в миллиметрах "
            "(например, <code>10</code>):"
        )
    elif thread_type == "inch":
        await state.set_state(ThreadFlow.entering_inch_diameter)
        await callback.message.answer(
            "<b>Дюймовая резьба · диаметр</b>\n"
            "Введите диаметр в дюймах "
            "(например, <code>0.5</code>):"
        )
    else:
        await state.set_state(ThreadFlow.entering_pipe_size)
        await callback.message.answer(
            "<b>Трубная резьба · размер</b>\n"
            "Введите размер: <code>1/2</code>, <code>3/4</code> или <code>1</code>:"
        )


@router.message(ThreadFlow.entering_pipe_size)
async def enter_pipe_size(
    message: Message,
    state: FSMContext,
) -> None:
    size = (message.text or "").strip()
    pipe_sizes = {
        "1/2": ("1/2", 0.5),
        "3/4": ("3/4", 0.75),
        "1": ("1", 1.0),
    }
    if size not in pipe_sizes:
        await message.answer(
            "Введите размер строго в одном из вариантов: 1/2, 3/4 или 1."
        )
        return

    label, size_inch = pipe_sizes[size]
    tpi = 14.0 if size_inch < 1.0 else 11.0
    await state.update_data(
        pipe_label=label,
        major_mm=size_inch * 25.4,
        pitch_mm=25.4 / tpi,
        tpi=tpi,
    )
    await state.set_state(ThreadFlow.entering_pipe_length)
    await message.answer(
        f"<b>Трубная резьба G{label}</b>\n"
        f"TPI: <b>{int(tpi)}</b>\n"
        "Введите длину резьбы в миллиметрах "
        "(например, <code>20</code>):"
    )


@router.message(ThreadFlow.entering_metric_major)
async def enter_metric_major(message: Message, state: FSMContext) -> None:
    major = parse_diameter(message.text or "")
    if major is None or major > 5000:
        await message.answer("Введите корректный номинальный диаметр в мм.")
        return
    await state.update_data(major_mm=major)
    await state.set_state(ThreadFlow.entering_metric_pitch)
    await message.answer(
        "Введите шаг резьбы в миллиметрах "
        "(например, <code>1,5</code>):"
    )


@router.message(ThreadFlow.entering_metric_pitch)
async def enter_metric_pitch(message: Message, state: FSMContext) -> None:
    pitch = parse_diameter(message.text or "")
    if pitch is None or pitch > 100:
        await message.answer("Введите корректный шаг резьбы в мм.")
        return
    await state.update_data(pitch_mm=pitch)
    await state.set_state(ThreadFlow.entering_metric_length)
    await message.answer(
        "Введите длину резьбы в миллиметрах "
        "(например, <code>20</code>):"
    )


@router.message(ThreadFlow.entering_metric_length)
async def enter_metric_length(message: Message, state: FSMContext) -> None:
    length = parse_diameter(message.text or "")
    if length is None or length > 5000:
        await message.answer("Введите корректную длину резьбы в мм.")
        return
    await state.update_data(length_mm=length)
    await state.set_state(ThreadFlow.choosing_side)
    await message.answer(
        "Выберите исполнение резьбы:",
        reply_markup=thread_side_keyboard(),
    )


@router.message(ThreadFlow.entering_inch_diameter)
async def enter_inch_diameter(message: Message, state: FSMContext) -> None:
    diameter_inch = parse_diameter(message.text or "")
    if diameter_inch is None or diameter_inch > 100:
        await message.answer("Введите корректный диаметр в дюймах.")
        return
    await state.update_data(major_mm=diameter_inch * 25.4)
    await state.set_state(ThreadFlow.entering_inch_tpi)
    await message.answer(
        "Введите TPI — число нитей на дюйм "
        "(например, <code>13</code>):"
    )


@router.message(ThreadFlow.entering_inch_tpi)
async def enter_inch_tpi(message: Message, state: FSMContext) -> None:
    tpi = parse_diameter(message.text or "")
    if tpi is None or tpi > 1000:
        await message.answer("Введите корректное значение TPI.")
        return
    await state.update_data(pitch_mm=25.4 / tpi, tpi=tpi)
    await state.set_state(ThreadFlow.entering_inch_length)
    await message.answer(
        "Введите длину резьбы в миллиметрах "
        "(например, <code>20</code>):"
    )


@router.message(ThreadFlow.entering_inch_length)
async def enter_inch_length(message: Message, state: FSMContext) -> None:
    length = parse_diameter(message.text or "")
    if length is None or length > 5000:
        await message.answer("Введите корректную длину резьбы в мм.")
        return
    await state.update_data(length_mm=length)
    await state.set_state(ThreadFlow.choosing_side)
    await message.answer(
        "Выберите исполнение резьбы:",
        reply_markup=thread_side_keyboard(),
    )


@router.message(ThreadFlow.entering_pipe_length)
async def enter_pipe_length(message: Message, state: FSMContext) -> None:
    length = parse_diameter(message.text or "")
    if length is None or length > 5000:
        await message.answer("Введите корректную длину резьбы в мм.")
        return
    await state.update_data(length_mm=length)
    await state.set_state(ThreadFlow.choosing_side)
    await message.answer(
        "Выберите исполнение резьбы:",
        reply_markup=thread_side_keyboard(),
    )


@router.callback_query(
    ThreadFlow.choosing_side,
    F.data.startswith("thread_side:"),
)
async def select_thread_side(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    side = callback.data.split(":", maxsplit=1)[1]
    if side not in {"external", "internal"}:
        await callback.answer("Неизвестное исполнение", show_alert=True)
        return

    data = await state.get_data()
    thread_type = data.get("thread_type")
    major_mm = data.get("major_mm")
    pitch_mm = data.get("pitch_mm")
    length_mm = data.get("length_mm")
    if (
        thread_type not in {"metric", "inch", "pipe"}
        or not isinstance(major_mm, (int, float))
        or not isinstance(pitch_mm, (int, float))
        or not isinstance(length_mm, (int, float))
    ):
        await state.clear()
        await callback.answer("Состояние расчёта сбросилось", show_alert=True)
        return

    values = calculate_thread_values(float(major_mm), float(pitch_mm), side)
    gcode = format_thread_gcode(
        thread_type,
        float(major_mm),
        float(pitch_mm),
        float(length_mm),
        side,
        values,
        nominal_label=data.get("pipe_label"),
    )
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"<pre>{escape(gcode)}</pre>",
            reply_markup=thread_result_keyboard(),
        )


@router.callback_query(
    TurningFlow.choosing_material,
    F.data.startswith("material_page:"),
)
async def navigate_material_page(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    current_page = int(data.get("material_page", 0))
    if callback.data == "material_page:prev":
        page = max(0, current_page - 1)
    elif callback.data == "material_page:next":
        page = min(material_page_count() - 1, current_page + 1)
    else:
        await callback.answer("Неизвестная страница", show_alert=True)
        return

    if page == current_page:
        message = "Это первая страница" if page == 0 else "Это последняя страница"
        await callback.answer(message)
        return

    await state.update_data(material_page=page)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            material_page_text(page),
            reply_markup=material_keyboard(page),
        )


@router.callback_query(
    TurningFlow.choosing_material,
    F.data.startswith("material:"),
)
async def select_material(callback: CallbackQuery, state: FSMContext) -> None:
    material_key = callback.data.split(":", maxsplit=1)[1]
    if material_key not in MATERIALS:
        await callback.answer("Неизвестный материал", show_alert=True)
        return
    await state.update_data(material_key=material_key)
    await state.set_state(TurningFlow.choosing_tool)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>Шаг 2 из 5 · инструмент</b>\n"
            "Выберите материал режущей части:",
            reply_markup=tool_keyboard(),
        )


@router.callback_query(
    TurningFlow.choosing_tool,
    F.data.startswith("tool:"),
)
async def select_tool(callback: CallbackQuery, state: FSMContext) -> None:
    tool_key = callback.data.split(":", maxsplit=1)[1]
    if tool_key not in TOOL_LABELS:
        await callback.answer("Неизвестный инструмент", show_alert=True)
        return
    await state.update_data(tool_key=tool_key)
    await state.set_state(TurningFlow.choosing_mode)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>Шаг 3 из 5 · обработка</b>\nВыберите тип обработки:",
            reply_markup=mode_keyboard(),
        )


@router.callback_query(
    TurningFlow.choosing_mode,
    F.data.startswith("mode:"),
)
async def select_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode_key = callback.data.split(":", maxsplit=1)[1]
    if mode_key not in MODE_LABELS:
        await callback.answer("Неизвестный тип обработки", show_alert=True)
        return
    await state.update_data(mode_key=mode_key)
    await state.set_state(TurningFlow.choosing_machine)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>Шаг 4 из 5 · станок</b>\nВыберите станок:",
            reply_markup=machine_keyboard(),
        )


@router.callback_query(
    TurningFlow.choosing_machine,
    F.data.startswith("machine:"),
)
async def select_machine(callback: CallbackQuery, state: FSMContext) -> None:
    machine_key = callback.data.split(":", maxsplit=1)[1]
    if machine_key not in MACHINES:
        await callback.answer("Неизвестный станок", show_alert=True)
        return
    await state.update_data(machine_key=machine_key)
    await state.set_state(TurningFlow.entering_diameter)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>Шаг 5 из 5 · диаметр</b>\n"
            "Введите диаметр заготовки в миллиметрах "
            "(например, <code>40</code> или <code>12,5</code>):"
        )


@router.message(TurningFlow.entering_diameter)
async def enter_diameter(message: Message, state: FSMContext) -> None:
    diameter = parse_diameter(message.text or "")
    if diameter is None or diameter > 5000:
        await message.answer(
            "Введите диаметр больше 0 и не более 5000 мм. "
            "Для дробного значения можно использовать запятую."
        )
        return

    data = await state.get_data()
    material_key = data.get("material_key")
    tool_key = data.get("tool_key")
    mode_key = data.get("mode_key")
    machine_key = data.get("machine_key")
    if (
        material_key not in MATERIALS
        or tool_key not in TOOL_LABELS
        or mode_key not in MODE_LABELS
        or machine_key not in MACHINES
    ):
        await state.clear()
        await message.answer(
            "Состояние расчёта сбросилось. Нажмите «📊 Расчёт режимов», "
            "чтобы начать заново."
        )
        return

    values = calculate_turning(material_key, tool_key, mode_key, diameter)
    await state.update_data(
        calculated_values=values,
        target_diameter_mm=float(diameter),
    )
    await state.set_state(GCodeFlow.result)
    await message.answer(
        format_result(
            material_key,
            tool_key,
            mode_key,
            machine_key,
            diameter,
            values,
        ),
        reply_markup=result_keyboard(),
    )


@router.callback_query(F.data.startswith("gcode:"))
async def start_gcode_generation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if (
        data.get("material_key") not in MATERIALS
        or data.get("tool_key") not in TOOL_LABELS
        or data.get("mode_key") not in MODE_LABELS
        or data.get("machine_key") not in MACHINES
        or not isinstance(data.get("calculated_values"), dict)
    ):
        await callback.answer(
            "Сначала выполните расчёт режимов.",
            show_alert=True,
        )
        return

    await state.set_state(GCodeFlow.entering_stock_diameter)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>G-код Fanuc · диаметр заготовки</b>\n"
            "Введите диаметр заготовки в миллиметрах "
            "(например, <code>40</code> или <code>12,5</code>):"
        )


@router.message(GCodeFlow.entering_stock_diameter)
async def enter_gcode_stock_diameter(
    message: Message,
    state: FSMContext,
) -> None:
    stock_diameter = parse_diameter(message.text or "")
    if stock_diameter is None or stock_diameter > 5000:
        await message.answer(
            "Введите диаметр заготовки больше 0 и не более 5000 мм. "
            "Для дробного значения можно использовать запятую."
        )
        return

    await state.update_data(stock_diameter=stock_diameter)
    await state.set_state(GCodeFlow.entering_length)
    await message.answer(
        "<b>Г-код Fanuc · длина обработки</b>\n"
        "Введите длину обработки в миллиметрах "
        "(например, <code>80</code> или <code>125,5</code>):"
    )


@router.message(GCodeFlow.entering_length)
async def enter_gcode_length(
    message: Message,
    state: FSMContext,
) -> None:
    length = parse_diameter(message.text or "")
    if length is None or length > 5000:
        await message.answer(
            "Введите длину обработки больше 0 и не более 5000 мм. "
            "Для дробного значения можно использовать запятую."
        )
        return

    data = await state.get_data()
    material_key = data.get("material_key")
    tool_key = data.get("tool_key")
    mode_key = data.get("mode_key")
    calculated_values = data.get("calculated_values")
    stock_diameter = data.get("stock_diameter")
    target_diameter = data.get("target_diameter_mm")
    if (
        material_key not in MATERIALS
        or tool_key not in TOOL_LABELS
        or mode_key not in MODE_LABELS
        or not isinstance(calculated_values, dict)
        or not isinstance(stock_diameter, (int, float))
        or not isinstance(target_diameter, (int, float))
    ):
        await state.clear()
        await message.answer(
            "Состояние G-кода сбросилось. Выполните новый расчёт режимов."
        )
        return

    gcode = format_fanuc_gcode(
        material_key,
        mode_key,
        float(stock_diameter),
        float(target_diameter),
        length,
        calculated_values,
    )
    await state.set_state(GCodeFlow.result)
    await message.answer(
        f"<pre>{escape(gcode)}</pre>",
        reply_markup=gcode_result_keyboard(),
    )


@router.callback_query(F.data == "new_calculation")
async def new_calculation(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await start_calculation(callback.message, state)


@router.message(Command("help"))
async def command_help(message: Message) -> None:
    await message.answer(
        "Нажмите «📊 Расчёт режимов», выберите материал, инструмент и вид "
        "обработки, выберите станок, затем введите диаметр. Бот рассчитывает "
        "ориентировочные параметры для точения. Для отмены используйте /cancel."
    )


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения.")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    logger.info("CNC Telegram-бот запущен через long polling")
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
