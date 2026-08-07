import os
import json
import asyncio
from aiohttp import web
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

TIMEZONE = ZoneInfo("Europe/Moscow")

SHEET_NAME = "Учет зарплат"

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not ADMIN_ID:
    print("ВНИМАНИЕ: ADMIN_ID пока не задан. Позже обязательно добавим его.")


# =========================
# GOOGLE SHEETS
# =========================

def get_google_client():
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

    if not credentials_json:
        raise RuntimeError("Не задан GOOGLE_CREDENTIALS_JSON")

    data = json.loads(credentials_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        data,
        scopes=scopes,
    )

    return gspread.authorize(credentials)


def get_spreadsheet():
    client = get_google_client()
    return client.open_by_key("1q8d8sTAlxojrzKFHdzyayhGDaoZlzg6NhfJIabiW9ZY")


def get_sheet(name, headers):
    spreadsheet = get_spreadsheet()

    try:
        sheet = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(
            title=name,
            rows=1000,
            cols=len(headers),
        )

    if not sheet.row_values(1):
        sheet.append_row(headers)

    return sheet


def init_sheets():
    get_sheet(
        "Сотрудники",
        ["ID", "Имя", "Активен"],
    )

    get_sheet(
        "Объекты",
        ["ID", "Название", "Активен"],
    )

    get_sheet(
        "Выходы",
        [
            "Дата",
            "Сотрудник",
            "Объект план",
            "Объект факт",
            "План",
            "Факт",
            "Заработок",
            "Комментарий",
        ],
    )

    get_sheet(
        "Авансы",
        [
            "Дата",
            "Сотрудник",
            "Сумма",
            "Комментарий",
        ],
    )

    get_sheet(
        "Итоги",
        [
            "Период",
            "Сотрудник",
            "Выходов",
            "Заработано",
            "Авансы",
            "К выплате",
        ],
    )


# =========================
# TELEGRAM
# =========================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


def is_admin(user_id: int) -> bool:
    if not ADMIN_ID:
        return True

    return user_id == int(ADMIN_ID)


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 План на завтра"),
                KeyboardButton(text="✅ Закрыть день"),
            ],
            [
                KeyboardButton(text="👷 Сотрудники"),
                KeyboardButton(text="🏗️ Объекты"),
            ],
            [
                KeyboardButton(text="💸 Авансы"),
                KeyboardButton(text="📊 Отчёты"),
            ],
        ],
        resize_keyboard=True,
    )


# =========================
# СОСТОЯНИЯ
# =========================

class EmployeeState(StatesGroup):
    waiting_name = State()


class ObjectState(StatesGroup):
    waiting_name = State()


class PlanState(StatesGroup):
    choosing_employee = State()
    choosing_object = State()


class CloseDayState(StatesGroup):
    choosing_employee = State()
    choosing_status = State()
    choosing_object = State()
    waiting_salary = State()
    waiting_advance = State()
    waiting_comment = State()


class AdvanceState(StatesGroup):
    choosing_employee = State()
    waiting_amount = State()
    waiting_comment = State()


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def today_str():
    return datetime.now(TIMEZONE).strftime("%d.%m.%Y")


def tomorrow_str():
    return (datetime.now(TIMEZONE) + timedelta(days=1)).strftime("%d.%m.%Y")


def get_employees():
    sheet = get_sheet(
        "Сотрудники",
        ["ID", "Имя", "Активен"],
    )

    rows = sheet.get_all_records()

    result = []

    for row in rows:
        if str(row.get("Активен", "Да")).lower() != "нет":
            name = str(row.get("Имя", "")).strip()

            if name:
                result.append(name)

    return result


def get_objects():
    sheet = get_sheet(
        "Объекты",
        ["ID", "Название", "Активен"],
    )

    rows = sheet.get_all_records()

    result = []

    for row in rows:
        if str(row.get("Активен", "Да")).lower() != "нет":
            name = str(row.get("Название", "")).strip()

            if name:
                result.append(name)

    return result


def add_employee(name):
    sheet = get_sheet(
        "Сотрудники",
        ["ID", "Имя", "Активен"],
    )

    employees = get_employees()

    if name in employees:
        return False

    next_id = len(sheet.get_all_records()) + 1

    sheet.append_row([
        next_id,
        name,
        "Да",
    ])

    return True


def add_object(name):
    sheet = get_sheet(
        "Объекты",
        ["ID", "Название", "Активен"],
    )

    objects = get_objects()

    if name in objects:
        return False

    next_id = len(sheet.get_all_records()) + 1

    sheet.append_row([
        next_id,
        name,
        "Да",
    ])

    return True


def employee_keyboard():
    employees = get_employees()

    buttons = []

    for name in employees:
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"emp:{name}",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def object_keyboard(prefix="obj"):
    objects = get_objects()

    buttons = []

    for name in objects:
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"{prefix}:{name}",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "👋 Привет!\n\n"
        "Это бот «Учет зарплат».\n\n"
        "Здесь мы будем отмечать выходы сотрудников, "
        "объекты, заработок, авансы и комментарии.\n\n"
        "Начнём с настройки сотрудников и объектов.",
        reply_markup=main_menu(),
    )


@dp.message(Command("id"))
async def get_id(message: Message):
    await message.answer(
        f"Твой Telegram ID:\n{message.from_user.id}"
    )


# =========================
# СОТРУДНИКИ
# =========================

@dp.message(F.text == "👷 Сотрудники")
async def employees_menu(message: Message):
    if not is_admin(message.from_user.id):
        return

    employees = get_employees()

    if not employees:
        text = "👷 Сотрудников пока нет."
    else:
        text = "👷 Сотрудники:\n\n"

        for i, name in enumerate(employees, 1):
            text += f"{i}. {name}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить сотрудника",
                    callback_data="add_employee",
                )
            ]
        ]
    )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "add_employee")
async def add_employee_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.set_state(EmployeeState.waiting_name)

    await callback.message.answer(
        "Напиши имя сотрудника:"
    )


@dp.message(EmployeeState.waiting_name)
async def add_employee_finish(message: Message, state: FSMContext):
    name = message.text.strip()

    if add_employee(name):
        await message.answer(
            f"✅ Сотрудник «{name}» добавлен.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer(
            "⚠️ Такой сотрудник уже есть."
        )

    await state.clear()


# =========================
# ОБЪЕКТЫ
# =========================

@dp.message(F.text == "🏗️ Объекты")
async def objects_menu(message: Message):
    if not is_admin(message.from_user.id):
        return

    objects = get_objects()

    if not objects:
        text = "🏗️ Объектов пока нет."
    else:
        text = "🏗️ Объекты:\n\n"

        for i, name in enumerate(objects, 1):
            text += f"{i}. {name}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить объект",
                    callback_data="add_object",
                )
            ]
        ]
    )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "add_object")
async def add_object_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.set_state(ObjectState.waiting_name)

    await callback.message.answer(
        "Напиши название объекта:"
    )


@dp.message(ObjectState.waiting_name)
async def add_object_finish(message: Message, state: FSMContext):
    name = message.text.strip()

    if add_object(name):
        await message.answer(
            f"✅ Объект «{name}» добавлен.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer(
            "⚠️ Такой объект уже есть."
        )

    await state.clear()


# =========================
# ПЛАН НА ЗАВТРА
# =========================

@dp.message(F.text == "📅 План на завтра")
async def tomorrow_plan(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    employees = get_employees()

    if not employees:
        await message.answer(
            "Сначала добавь сотрудников."
        )
        return

    await state.update_data(
        employees=employees,
        index=0,
        plans=[],
    )

    await state.set_state(PlanState.choosing_object)

    employee = employees[0]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏖️ Выходной",
                    callback_data="plan:dayoff",
                )
            ]
        ] + [
            [
                InlineKeyboardButton(
                    text=obj,
                    callback_data=f"planobj:{obj}",
                )
            ]
            for obj in get_objects()
        ]
    )

    await message.answer(
        f"📅 План на {tomorrow_str()}\n\n"
        f"Куда завтра выходит:\n"
        f"👷 {employee}",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("planobj:"))
async def plan_object(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    obj = callback.data.split(":", 1)[1]

    await save_plan(callback.message, state, obj)


@dp.callback_query(F.data == "plan:dayoff")
async def plan_dayoff(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await save_plan(
        callback.message,
        state,
        "Выходной",
    )


async def save_plan(message, state, obj):
    data = await state.get_data()

    employees = data["employees"]
    index = data["index"]
    plans = data["plans"]

    employee = employees[index]

    plans.append({
        "employee": employee,
        "object": obj,
    })

    index += 1

    if index >= len(employees):
        await state.clear()

        await message.answer(
            "✅ План на завтра сохранён.",
            reply_markup=main_menu(),
        )

        return

    await state.update_data(
        index=index,
        plans=plans,
    )

    employee = employees[index]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏖️ Выходной",
                    callback_data="plan:dayoff",
                )
            ]
        ] + [
            [
                InlineKeyboardButton(
                    text=obj,
                    callback_data=f"planobj:{obj}",
                )
            ]
            for obj in get_objects()
        ]
    )

    await message.answer(
        f"👷 {employee}\n"
        f"Куда выходит завтра?",
        reply_markup=keyboard,
    )


# =========================
# ЗАКРЫТИЕ ДНЯ
# =========================

@dp.message(F.text == "✅ Закрыть день")
async def close_day(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    employees = get_employees()

    if not employees:
        await message.answer(
            "Сначала добавь сотрудников."
        )
        return

    await state.update_data(
        employees=employees,
        index=0,
        records=[],
    )

    await state.set_state(CloseDayState.choosing_status)

    await ask_status(message, state)


async def ask_status(message, state):
    data = await state.get_data()

    employee = data["employees"][data["index"]]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Вышел",
                    callback_data="status:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Не вышел",
                    callback_data="status:no",
                ),
            ]
        ]
    )

    await message.answer(
        f"📅 {today_str()}\n\n"
        f"👷 {employee}\n"
        f"Вышел сегодня?",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "status:yes")
async def status_yes(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.update_data(status="Вышел")

    objects = get_objects()

    if not objects:
        await callback.message.answer(
            "Сначала добавь хотя бы один объект."
        )
        return

    await state.set_state(CloseDayState.choosing_object)

    await callback.message.answer(
        "🏗️ На каком объекте работал?",
        reply_markup=object_keyboard("factobj"),
    )


@dp.callback_query(F.data == "status:no")
async def status_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()

    data.setdefault("records", [])

    data["records"].append({
        "date": today_str(),
        "employee": data["employees"][data["index"]],
        "object": "",
        "status": "Не вышел",
        "salary": 0,
        "advance": 0,
        "comment": "",
    })

    index = data["index"] + 1

    if index >= len(data["employees"]):
        await save_day_records(callback.message, data)
        await state.clear()
        return

    await state.update_data(
        index=index,
        records=data["records"],
    )

    await ask_status(callback.message, state)


@dp.callback_query(F.data.startswith("factobj:"))
async def fact_object(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    obj = callback.data.split(":", 1)[1]

    await state.update_data(object=obj)

    await state.set_state(CloseDayState.waiting_salary)

    await callback.message.answer(
        "💰 Сколько заработал сегодня?\n\n"
        "Например: 8500"
    )


@dp.message(CloseDayState.waiting_salary)
async def salary_entered(message: Message, state: FSMContext):
    try:
        salary = float(
            message.text.replace(" ", "").replace(",", ".")
        )
    except ValueError:
        await message.answer(
            "Напиши сумму числом. Например: 8500"
        )
        return

    await state.update_data(salary=salary)

    await state.set_state(CloseDayState.waiting_advance)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Нет аванса",
                    callback_data="advance:no",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Ввести аванс",
                    callback_data="advance:yes",
                )
            ]
        ]
    )

    await message.answer(
        "💸 Был сегодня аванс?",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "advance:no")
async def no_advance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.update_data(advance=0)

    await ask_comment(callback.message, state)


@dp.callback_query(F.data == "advance:yes")
async def yes_advance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await callback.message.answer(
        "💸 Введи сумму аванса:"
    )

    await state.set_state(CloseDayState.waiting_advance)


@dp.message(CloseDayState.waiting_advance)
async def advance_entered(message: Message, state: FSMContext):
    try:
        amount = float(
            message.text.replace(" ", "").replace(",", ".")
        )
    except ValueError:
        await message.answer(
            "Напиши сумму числом. Например: 2000"
        )
        return

    await state.update_data(advance=amount)

    await ask_comment(message, state)


async def ask_comment(message, state):
    await state.set_state(CloseDayState.waiting_comment)

    await message.answer(
        "📝 Комментарий?\n\n"
        "Если комментария нет — напиши: нет"
    )


@dp.message(CloseDayState.waiting_comment)
async def comment_entered(message: Message, state: FSMContext):
    data = await state.get_data()

    comment = message.text.strip()

    if comment.lower() == "нет":
        comment = ""

    data.setdefault("records", [])

    data["records"].append({
        "date": today_str(),
        "employee": data["employees"][data["index"]],
        "object": data.get("object", ""),
        "status": "Вышел",
        "salary": data.get("salary", 0),
        "advance": data.get("advance", 0),
        "comment": comment,
    })

    index = data["index"] + 1

    if index >= len(data["employees"]):
        await save_day_records(message, data)
        await state.clear()
        return

    await state.update_data(
        index=index,
        records=data["records"],
    )

    await ask_status(message, state)


async def save_day_records(message, data):
    sheet = get_sheet(
        "Выходы",
        [
            "Дата",
            "Сотрудник",
            "Объект план",
            "Объект факт",
            "План",
            "Факт",
            "Заработок",
            "Комментарий",
        ],
    )

    advance_sheet = get_sheet(
        "Авансы",
        [
            "Дата",
            "Сотрудник",
            "Сумма",
            "Комментарий",
        ],
    )

    for record in data["records"]:
        sheet.append_row([
            record["date"],
            record["employee"],
            "",
            record["object"],
            "",
            record["status"],
            record["salary"],
            record["comment"],
        ])

        if record["advance"]:
            advance_sheet.append_row([
                record["date"],
                record["employee"],
                record["advance"],
                record["comment"],
            ])

    await message.answer(
        "✅ День закрыт.\n\n"
        "Все данные записаны в Google Таблицу.",
        reply_markup=main_menu(),
    )


# =========================
# АВАНСЫ
# =========================

@dp.message(F.text == "💸 Авансы")
async def advances_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    employees = get_employees()

    if not employees:
        await message.answer(
            "Сначала добавь сотрудников."
        )
        return

    await state.set_state(AdvanceState.choosing_employee)

    await message.answer(
        "💸 Выбери сотрудника:",
        reply_markup=employee_keyboard(),
    )


@dp.callback_query(F.data.startswith("emp:"))
async def advance_employee(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    employee = callback.data.split(":", 1)[1]

    await state.update_data(employee=employee)

    await state.set_state(AdvanceState.waiting_amount)

    await callback.message.answer(
        f"💸 Аванс для {employee}\n\n"
        f"Введи сумму:"
    )


@dp.message(AdvanceState.waiting_amount)
async def advance_amount(message: Message, state: FSMContext):
    try:
        amount = float(
            message.text.replace(" ", "").replace(",", ".")
        )
    except ValueError:
        await message.answer(
            "Введи сумму числом. Например: 5000"
        )
        return

    await state.update_data(amount=amount)

    await state.set_state(AdvanceState.waiting_comment)

    await message.answer(
        "📝 Комментарий к авансу?\n\n"
        "Если нет — напиши: нет"
    )


@dp.message(AdvanceState.waiting_comment)
async def advance_comment(message: Message, state: FSMContext):
    data = await state.get_data()

    comment = message.text.strip()

    if comment.lower() == "нет":
        comment = ""

    sheet = get_sheet(
        "Авансы",
        [
            "Дата",
            "Сотрудник",
            "Сумма",
            "Комментарий",
        ],
    )

    sheet.append_row([
        today_str(),
        data["employee"],
        data["amount"],
        comment,
    ])

    await state.clear()

    await message.answer(
        "✅ Аванс записан в Google Таблицу.",
        reply_markup=main_menu(),
    )


# =========================
# ОТЧЁТЫ
# =========================

@dp.message(F.text == "📊 Отчёты")
async def reports(message: Message):
    if not is_admin(message.from_user.id):
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 1–15",
                    callback_data="report:first",
                ),
                InlineKeyboardButton(
                    text="📅 16–конец",
                    callback_data="report:second",
                ),
            ]
        ]
    )

    await message.answer(
        "📊 Выбери период:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("report:"))
async def report(callback: CallbackQuery):
    await callback.answer()

    now = datetime.now(TIMEZONE)

    if callback.data == "report:first":
        start_day = 1
        end_day = 15
        period_name = f"1–15.{now.month:02d}.{now.year}"
    else:
        start_day = 16
        next_month = now.replace(day=28) + timedelta(days=4)
        last_day = (next_month - timedelta(days=next_month.day)).day
        end_day = last_day
        period_name = f"16–{last_day:02d}.{now.month:02d}.{now.year}"

    sheet = get_sheet(
        "Выходы",
        [
            "Дата",
            "Сотрудник",
            "Объект план",
            "Объект факт",
            "План",
            "Факт",
            "Заработок",
            "Комментарий",
        ],
    )

    rows = sheet.get_all_records()

    result = {}

    for row in rows:
        try:
            date = datetime.strptime(
                str(row["Дата"]),
                "%d.%m.%Y",
            )
        except Exception:
            continue

        if date.year != now.year or date.month != now.month:
            continue

        if not (start_day <= date.day <= end_day):
            continue

        employee = str(row["Сотрудник"])

        if employee not in result:
            result[employee] = {
                "days": 0,
                "salary": 0,
                "advance": 0,
            }

        if str(row["Факт"]) == "Вышел":
            result[employee]["days"] += 1

        try:
            result[employee]["salary"] += float(
                row["Заработок"] or 0
            )
        except Exception:
            pass

    advance_sheet = get_sheet(
        "Авансы",
        [
            "Дата",
            "Сотрудник",
            "Сумма",
            "Комментарий",
        ],
    )

    advances = advance_sheet.get_all_records()

    for row in advances:
        try:
            date = datetime.strptime(
                str(row["Дата"]),
                "%d.%m.%Y",
            )
        except Exception:
            continue

        if date.year != now.year or date.month != now.month:
            continue

        if not (start_day <= date.day <= end_day):
            continue

        employee = str(row["Сотрудник"])

        if employee not in result:
            result[employee] = {
                "days": 0,
                "salary": 0,
                "advance": 0,
            }

        try:
            result[employee]["advance"] += float(
                row["Сумма"] or 0
            )
        except Exception:
            pass

    if not result:
        await callback.message.answer(
            f"📊 Период {period_name}\n\n"
            "Данных пока нет."
        )
        return

    text = f"📊 Период: {period_name}\n\n"

    for employee, data in result.items():
        salary = data["salary"]
        advance = data["advance"]
        to_pay = salary - advance

        text += (
            f"👷 {employee}\n"
            f"Выходов: {data['days']}\n"
            f"Заработано: {salary:,.0f} ₽\n"
            f"Авансы: {advance:,.0f} ₽\n"
            f"К выплате: {to_pay:,.0f} ₽\n\n"
        )

    await callback.message.answer(text)


# =========================
# НАПОМИНАНИЯ
# =========================

async def send_evening_reminder():
    if not ADMIN_ID:
        return

    await bot.send_message(
        int(ADMIN_ID),
        "🌙 Напоминание\n\n"
        "Не забудь указать, куда сотрудники выходят завтра "
        "и закрыть сегодняшний день.",
    )


async def send_morning_reminder():
    if not ADMIN_ID:
        return

    await bot.send_message(
        int(ADMIN_ID),
        "🌅 Доброе утро!\n\n"
        "Проверь, все ли сотрудники вышли по плану.",
    )


# =========================
# ЗАПУСК
# =========================

async def main():
    print("Запуск бота...")

    init_sheets()

    scheduler = AsyncIOScheduler(
        timezone=TIMEZONE
    )

    scheduler.add_job(
        send_evening_reminder,
        "cron",
        hour=20,
        minute=0,
    )

    scheduler.add_job(
        send_morning_reminder,
        "cron",
        hour=8,
        minute=0,
    )

    scheduler.start()

    print("Бот запущен.")

    await dp.start_polling(bot)

async def health(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()


if __name__ == "__main__":
    async def run():
        await start_web_server()
        await main()

    asyncio.run(run())
