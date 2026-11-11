from datetime import datetime, timezone
import html
import time
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from sqlalchemy import delete

from config import settings
from database.crud import (
    get_lead_by_external_id,
    get_lead_by_id,
    get_or_create_user,
    get_recent_leads,
    get_user,
    get_user_filters,
    get_user_status,
    get_utm_analytics,
    save_lead,
    toggle_user_active,
)
from database.models import Lead
from database.session import async_session
from parsers.base import RawLead
from services.matcher import build_lead_keyboard, format_lead_message, matches_filter
from services.pitcher import generate_pitch

router = Router()


async def render_welcome(event: Message | CallbackQuery):
    user = event.from_user
    user_id = user.id
    username = user.username

    # Parse referral code or UTM marketing source from /start <param> if it's a Message
    referred_by = None
    utm_source = None
    if isinstance(event, Message) and event.text and len(event.text.split()) > 1:
        param = event.text.split()[1].strip()
        if param.startswith("ref_"):
            try:
                ref_id = int(param.replace("ref_", ""))
                if ref_id != user_id:
                    referred_by = ref_id
            except ValueError:
                pass
        else:
            param_lower = param.lower()
            if param_lower.startswith(("utm_", "c_", "ad_", "promo_")) or "utm" in param_lower:
                utm_source = param[:128]

    db_user, created = await get_or_create_user(
        telegram_id=user_id,
        username=username,
        referred_by=referred_by,
        utm_source=utm_source,
    )

    if created and referred_by:
        try:
            from services.achievements import check_and_grant_achievements
            await check_and_grant_achievements(referred_by, bot=event.bot)
        except Exception:
            pass

    try:
        from services.achievements import check_and_grant_achievements
        await check_and_grant_achievements(user_id, bot=event.bot)
    except Exception:
        pass

    status = await get_user_status(user_id)
    is_active = getattr(db_user, "is_active", True)

    if status["is_premium"]:
        sub_text = "⚡ <b>PREMIUM</b> (0 сек задержка, безлимит)"
    elif status["is_trial"]:
        hours = status["trial_hours_left"]
        sub_text = f"⚡ <b>WELCOME TRIAL {hours}ч</b> (0 сек задержка активна)"
    else:
        sub_text = (
            f"⏳ <b>FREE</b> (задержка {settings.FREE_DELAY_SECONDS // 60} мин, "
            f"до {settings.FREE_MAX_DAILY_LEADS} лидов/день)"
        )

    radar_status_text = "🟢 <b>АКТИВЕН</b> (лиды приходят)" if is_active else "⏸ <b>НА ПАУЗЕ</b> (лиды отключены)"

    welcome_text = (
        "👋 <b>Добро пожаловать в Lead Radar!</b>\n\n"
        "⚡ <b>Перехват заказов с фриланс-бирж в реальном времени.</b>\n"
        "Мониторинг FL.ru и Freelancehunt за 0–2 секунды с момента публикации.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Тариф:</b> {sub_text}\n"
        f"📡 <b>Статус радара:</b> {radar_status_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 <i>Отклик в первые 3 минуты повышает вероятность взять заказ на 80%.</i>"
    )

    buttons = []
    if settings.WEBAPP_URL:
        buttons.append(
            [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        )

    prem_btn_text = "⚡ Закрепить 0 сек" if status["is_trial"] else "⚡ Включить Premium"
    toggle_btn_text = "⏸ Приостановить лиды" if is_active else "▶️ Возобновить лиды"

    buttons.extend([
        [
            InlineKeyboardButton(text="🎯 Фильтры", callback_data="open_filters"),
            InlineKeyboardButton(text=prem_btn_text, callback_data="buy_premium"),
        ],
        [
            InlineKeyboardButton(text="🧪 Тестовый лид", callback_data="test_lead"),
            InlineKeyboardButton(text=toggle_btn_text, callback_data="toggle_leads"),
        ],
        [
            InlineKeyboardButton(text="👤 Профиль и Рефералы", callback_data="open_profile"),
        ],
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await event.message.answer(welcome_text, parse_mode="HTML", reply_markup=keyboard)
        await event.answer()
    else:
        await event.answer(welcome_text, parse_mode="HTML", reply_markup=keyboard)


async def send_test_lead(event: Message | CallbackQuery):
    """Sends an instant full-scale test lead matching user filters to verify radar operation."""
    user = event.from_user
    user_id = user.id

    db_user, _ = await get_or_create_user(telegram_id=user_id, username=user.username)
    status = await get_user_status(user_id)
    user_filter = await get_user_filters(user_id)

    is_premium = status["is_premium"]
    is_trial = status["is_trial"]
    trial_hours = status.get("trial_hours_left", 24)

    # 1. Search for matching lead from existing leads in DB (excluding test leads)
    recent_leads = await get_recent_leads(limit=50, include_test=False)
    matching_lead: RawLead | None = None
    target_lead_id: int | None = None

    for l in recent_leads:
        lead_text = f"{l.title} {l.description}".lower()
        if matches_filter(
            lead_text=lead_text,
            keywords_str=user_filter.keywords,
            stop_words_str=user_filter.stop_words,
            min_budget=user_filter.min_budget,
            categories_str=user_filter.categories,
            lead_budget_str=l.budget,
            lead_category=l.category,
        ):
            target_lead_id = l.id
            matching_lead = RawLead(
                source=l.source,
                external_id=l.external_id,
                title=l.title,
                description=l.description,
                url=l.url,
                budget=l.budget,
                category=l.category,
                published_at=l.published_at or datetime.now(timezone.utc),
                raw_text=lead_text,
                id=l.id,
            )
            break

    # 2. If no matching lead in DB, synthesize a realistic test lead
    if not matching_lead:
        cats = [c.strip().lower() for c in (user_filter.categories or "all").split(",") if c.strip()]
        cat = "bot"
        for c in cats:
            if c != "all":
                cat = c
                break

        kw = ""
        if user_filter.keywords:
            kws = [k.strip() for k in user_filter.keywords.split(",") if k.strip()]
            if kws:
                kw = kws[0]

        budget_amount = max(35000, (user_filter.min_budget or 0) + 10000)
        budget_str = f"{budget_amount:,} ₽".replace(",", " ")

        lead_templates = {
            "bot": (
                "Разработка Telegram-бота для сервиса доставки с админ-панелью",
                "Нужен Telegram-бот на Python (aiogram) для автоматического приёма заказов, интеграции с CRM и базой данных PostgreSQL. Требуется интуитивная веб-панель для менеджеров и приём онлайн-оплаты.",
            ),
            "python": (
                "Разработка парсера каталога маркетплейса на Python",
                "Требуется написать быстрый многопоточный парсер на Python с обходом Cloudflare и капчи. Выгрузка собранных карточек в Excel и базу данных с регулярным запуском по расписанию.",
            ),
            "frontend": (
                "Адаптивная верстка современного лендинга на React / Tailwind",
                "Необходимо сверстать многостраничный сайт по готовому дизайн-макету из Figma. Обязательны чистый код, адаптивность под все устройства (iOS/Android/Desktop) и высокая скорость загрузки.",
            ),
            "backend": (
                "Проектирование REST API и бэкенда для мобильного приложения",
                "Ищем бэкенд-разработчика для реализации микросервисной архитектуры на FastAPI / Node.js. Авторизация JWT, интеграция с внешними платежными шлюзами и документация Swagger.",
            ),
            "mobile": (
                "Разработка мобильного приложения для фитнес-трекера (Flutter / iOS / Android)",
                "Требуется создать кроссплатформенное приложение на Flutter с офлайн-синхронизацией, пуш-уведомлениями и современным лаконичным интерфейсом.",
            ),
            "design": (
                "Дизайн мобильного приложения и веб-сервиса в Figma (UI/UX)",
                "Нужен продуманный UI/UX дизайн в Figma: разработка дизайн-системы, интерактивного кликабельного прототипа и компонентов для передачи верстальщику.",
            ),
            "1c": (
                "Интеграция 1С:Управление торговлей с интернет-магазином",
                "Требуется настроить стабильный двусторонний обмен номенклатурой, остатками и заказами между 1С и сайтом. Исправить ошибки синхронизации и протестировать на тестовой копии.",
            ),
            "marketing": (
                "Настройка контекстной рекламы в Яндекс Директ и воронки продаж",
                "Необходимо провести аудит посадочной страницы, настроить поисковую и РСЯ кампании с оптимизацией стоимости целевого лида и подключением аналитики.",
            ),
            "copywriting": (
                "Серия экспертных статей и текстов для блога IT-компании",
                "Требуется автор с глубоким пониманием технологий для написания вовлекающих статей. Без воды, с разбором реальных кейсов и примерами кода/архитектуры.",
            ),
            "analytics": (
                "Построение сквозного аналитического дашборда в Power BI",
                "Требуется объединить данные из CRM, рекламных кабинетов и базы данных в единый интерактивный дашборд. Настроить расчет ключевых метрик (ROI, LTV, CAC).",
            ),
            "devops": (
                "Настройка CI/CD пайплайна и Docker-инфраструктуры в облаке",
                "Требуется контейнеризировать микросервисы, настроить автодеплой через GitLab CI/CD и сконфигурировать мониторинг с защитой от сбоев.",
            ),
            "gamedev": (
                "Разработка игровой механики и интерфейса для мобильной игры",
                "Ищем разработчика (Unity / Godot) для реализации ключевого геймплейного цикла, анимаций персонажа и интеграции системы сохранений.",
            ),
            "other": (
                "Техническая реализация и запуск проекта под ключ",
                "Требуется ответственный специалист для реализации задачи по техническому заданию. Важны соблюдение сроков, аккуратный подход и прозрачная коммуникация.",
            ),
        }

        default_title, default_desc = lead_templates.get(cat, lead_templates["bot"])
        test_title = f"{default_title} ({kw})" if kw else default_title
        test_ext_id = f"test_{user_id}_{int(time.time())}"
        test_url = "https://www.fl.ru/projects/sample"
        pub_now = datetime.now(timezone.utc)

        # Remove prior synthetic test leads for this user to keep DB clean
        async with async_session() as session:
            await session.execute(
                delete(Lead).where(Lead.external_id.like(f"test_{user_id}_%"))
            )
            await session.commit()

        await save_lead(
            source="fl_ru",
            external_id=test_ext_id,
            title=test_title,
            description=default_desc,
            url=test_url,
            budget=budget_str,
            category=cat,
            published_at=pub_now,
        )
        saved_lead = await get_lead_by_external_id(test_ext_id)
        target_lead_id = saved_lead.id if saved_lead else None

        matching_lead = RawLead(
            source="fl_ru",
            external_id=test_ext_id,
            title=test_title,
            description=default_desc,
            url=test_url,
            budget=budget_str,
            category=cat,
            published_at=pub_now,
            raw_text=f"{test_title} {default_desc}".lower(),
            id=target_lead_id,
        )

    card_text = format_lead_message(
        lead=matching_lead,
        is_premium=is_premium,
        is_trial=is_trial,
        trial_hours=trial_hours,
    )
    header = "🧪 <b>ТЕСТОВЫЙ ЛИД ПО ВАШИМ ФИЛЬТРАМ:</b>\n<i>Проверьте оформление карточки и генератор откликов!</i>\n\n"
    full_text = header + card_text

    kb = build_lead_keyboard(
        lead=matching_lead,
        is_premium=is_premium,
        is_trial=is_trial,
        lead_id=target_lead_id,
    )

    await event.bot.send_message(
        chat_id=user_id,
        text=full_text,
        parse_mode="HTML",
        reply_markup=kb,
    )

    if isinstance(event, CallbackQuery):
        await event.answer("🧪 Тестовый лид доставлен!")


@router.message(CommandStart())
async def cmd_start(message: Message):
    await render_welcome(message)


@router.message(Command("test_lead"))
@router.callback_query(F.data == "test_lead")
async def cmd_test_lead(event: Message | CallbackQuery):
    await send_test_lead(event)


@router.callback_query(F.data.startswith("pitch_"))
async def handle_pitch_callback(callback: CallbackQuery):
    await callback.answer("✅ Формируем отклик...")
    ref = callback.data.replace("pitch_", "")
    lead = None
    if ref.isdigit():
        lead = await get_lead_by_id(int(ref))
    if not lead and ref and ref != "sample":
        lead = await get_lead_by_external_id(ref)

    if lead:
        pitch = generate_pitch(
            title=lead.title,
            description=lead.description,
            budget=lead.budget,
            category=lead.category,
        )
    else:
        msg_text = callback.message.text or callback.message.caption or ""
        extracted_title = ""
        if "📌" in msg_text:
            try:
                parts = msg_text.split("📌")
                if len(parts) > 1:
                    extracted_title = parts[1].split("\n")[0].strip()
            except Exception:
                pass
        pitch = generate_pitch(
            title=extracted_title or "Заказ на бирже",
            description="",
            budget="",
        )

    safe_pitch = html.escape(pitch)
    reply_text = (
        "💡 <b>Готовый шаблон отклика под этот заказ:</b>\n\n"
        f"<code>{safe_pitch}</code>\n\n"
        "👆 <i>Нажмите на текст выше, чтобы скопировать и отправить заказчику.</i>"
    )
    try:
        await callback.message.reply(reply_text, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=reply_text,
            parse_mode="HTML",
        )


@router.callback_query(F.data == "toggle_leads")
async def toggle_leads_callback(callback: CallbackQuery):
    new_state = await toggle_user_active(callback.from_user.id)
    toast_msg = "▶️ Радар включен. Лиды снова поступают!" if new_state else "⏸ Радар на паузе. Лиды приостановлены."
    await callback.answer(toast_msg, show_alert=True)
    if callback.message and callback.message.text and "Профиль" in callback.message.text:
        from handlers.profile import show_profile
        await show_profile(callback)
    else:
        await render_welcome(callback)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await render_welcome(callback)


@router.message(Command("stats"))
@router.message(Command("analytics"))
async def cmd_analytics(message: Message):
    user_id = message.from_user.id
    if not settings.is_admin(user_id):
        await message.answer("⛔ Доступ к статистике разрешен только администраторам.")
        return

    stats = await get_utm_analytics()
    if not stats:
        await message.answer("📊 <b>Статистика по источникам пуста.</b>", parse_mode="HTML")
        return

    total_users = sum(s["total_users"] for s in stats)
    total_active = sum(s["active_users"] for s in stats)
    total_prem = sum(s["premium_users"] for s in stats)
    total_trial = sum(s["trial_users"] for s in stats)
    total_prem_trial = sum(s["premium_or_trial"] for s in stats)

    lines = [
        "📊 <b>Сквозная аналитика UTM-источников</b>\n",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for item in stats:
        source = item["utm_source"]
        tot = item["total_users"]
        act = item["active_users"]
        prem = item["premium_users"]
        trial = item["trial_users"]
        pt = item["premium_or_trial"]
        conv = (act / tot * 100) if tot > 0 else 0

        lines.append(
            f"🏷 <b>{html.escape(source)}</b>\n"
            f"  👥 Регистраций: <b>{tot}</b>\n"
            f"  🟢 Активных: <b>{act}</b> ({conv:.1f}%)\n"
            f"  💎 Prem/Trial: <b>{pt}</b> (Prem: {prem} | Trial: {trial})\n"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"📈 <b>ИТОГО:</b>\n"
        f"  👥 Всего пользователей: <b>{total_users}</b>\n"
        f"  🟢 Активных радаров: <b>{total_active}</b>\n"
        f"  💎 На Premium/Trial: <b>{total_prem_trial}</b> (Prem: {total_prem} | Trial: {total_trial})"
    )

    await message.answer("\n".join(lines), parse_mode="HTML")

