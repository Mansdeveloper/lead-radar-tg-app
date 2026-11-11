import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from aiohttp.test_utils import AioHTTPTestCase
from aiogram.types import CallbackQuery, Message, SuccessfulPayment, User as TgUser
from sqlalchemy import delete

from config import settings
from database.crud import (
    get_or_create_user,
    get_user,
    get_user_filters,
    save_lead,
    update_user_full_filters,
)
from database.models import Achievement, Lead, User, UserFilter
from database.session import async_session, engine, ensure_sqlite_parent_dir, init_db
from handlers.filters import process_keywords, process_stop_words, render_filters_text, reset_filters_handler
from handlers.help import build_help_text, show_help
from handlers.payments import process_successful_payment
from handlers.profile import show_profile
from handlers.start import (
    ONBOARDING_CATEGORIES,
    back_to_menu_callback,
    handle_onboarding_category,
    render_welcome,
    toggle_leads_callback,
)
from main import (
    BOT_COMMANDS,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    configure_bot_profile,
)
from parsers.base import RawLead
from services.matcher import dispatch_lead, matches_filter
from webapp.server import create_webapp


class TestDatabasePersistencePath(unittest.TestCase):
    def test_ensure_sqlite_parent_dir_creates_nested_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "nested" / "data" / "lead_radar.db"
            db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
            self.assertFalse(db_file.parent.exists())
            ensure_sqlite_parent_dir(db_url)
            self.assertTrue(db_file.parent.exists())

    def test_ensure_sqlite_parent_dir_ignores_memory_and_relative(self):
        ensure_sqlite_parent_dir("sqlite+aiosqlite:///:memory:")
        ensure_sqlite_parent_dir("sqlite+aiosqlite:///lead_radar.db")
        ensure_sqlite_parent_dir("postgresql+asyncpg://user:pass@localhost/db")

    def test_ensure_sqlite_parent_dir_handles_permission_error(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("Read-only filesystem")):
            # Must not raise
            ensure_sqlite_parent_dir("sqlite+aiosqlite:////data/lead_radar.db")


class TestOnboardingAndSpamProtection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.test_user_ids = [870001, 870002, 870003, 870004, 870005]
        self.test_ext_ids = [
            "onb_real_python_1",
            "onb_real_copy_1",
            "onb_real_design_1",
        ]
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(Lead).where(Lead.external_id.in_(self.test_ext_ids)))
            for uid in self.test_user_ids:
                await s.execute(delete(Lead).where(Lead.external_id.like(f"test_{uid}_%")))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_user_ids)))
            await s.execute(delete(Lead).where(Lead.external_id.in_(self.test_ext_ids)))
            for uid in self.test_user_ids:
                await s.execute(delete(Lead).where(Lead.external_id.like(f"test_{uid}_%")))
            await s.commit()
        await engine.dispose()

    async def test_new_user_gets_categories_none_and_blocked_from_spam(self):
        user, created = await get_or_create_user(870001, username="newbie")
        self.assertTrue(created)
        uf = await get_user_filters(870001)
        self.assertEqual(uf.categories, "none")

        # Anti-spam check in matcher
        self.assertFalse(matches_filter("нужен python разработчик", keywords_str="", categories_str="none"))
        self.assertFalse(matches_filter("нужен python разработчик", keywords_str="python", categories_str="none"))

        # Legacy/onboarded user with 'all' still matches
        self.assertTrue(matches_filter("нужен python разработчик", keywords_str="", categories_str="all"))

        # dispatch_lead must NOT send leads to un-onboarded user (870001),
        # while sending to onboarded user (870002)
        await get_or_create_user(870002, username="onboarded_user")
        await update_user_full_filters(870002, keywords="", stop_words="", min_budget=0, categories="python")

        lead = RawLead(
            source="fl_ru",
            external_id="dispatch_guard_1",
            title="Разработка парсера на Python",
            description="Быстрый сбор данных с сайта",
            url="https://fl.ru/projects/1",
            budget="30 000 ₽",
            category="python",
            published_at=datetime.now(timezone.utc),
            raw_text="разработка парсера на python",
            id=999001,
        )
        mock_bot = AsyncMock()
        await dispatch_lead(mock_bot, lead)

        sent_chat_ids = [call.kwargs["chat_id"] for call in mock_bot.send_message.call_args_list]
        self.assertNotIn(870001, sent_chat_ids)
        self.assertIn(870002, sent_chat_ids)

    async def test_legacy_user_missing_filter_gets_all_fallback(self):
        # Simulate legacy user existing in DB without a UserFilter row
        async with async_session() as s:
            s.add(User(telegram_id=870005, username="legacy_no_filter"))
            await s.commit()

        _, created = await get_or_create_user(870005, username="legacy_no_filter")
        self.assertFalse(created)
        uf = await get_user_filters(870005)
        self.assertEqual(uf.categories, "all")

    async def test_first_start_shows_onboarding_keyboard(self):
        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=870001, is_bot=False, first_name="New", username="new_user")
        msg.text = "/start"
        msg.answer = AsyncMock()
        msg.bot = AsyncMock()

        await render_welcome(msg)
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        kb = msg.answer.call_args.kwargs["reply_markup"]

        self.assertIn("Выберите вашу специализацию", text)
        btn_texts = [b.text for row in kb.inline_keyboard for b in row]
        btn_cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        for label, cat in ONBOARDING_CATEGORIES:
            self.assertIn(label, btn_texts)
            self.assertIn(f"onboard_cat:{cat}", btn_cbs)

    async def test_onboard_cat_saves_filter_shows_menu_and_sends_real_lead(self):
        # Seed a real python lead in DB (far in the future so it's the freshest even on populated local DB)
        future_ts = datetime.now(timezone.utc) + timedelta(days=365)
        await save_lead(
            source="fl_ru",
            external_id="onb_real_python_1",
            title="Парсер маркетплейсов на Python + FastAPI",
            description="Нужен надежный парсер на Python с выгрузкой в PostgreSQL.",
            url="https://www.fl.ru/projects/999111",
            budget="45 000 ₽",
            category="python",
            published_at=future_ts,
        )

        await get_or_create_user(870003, username="py_dev")

        mock_bot = AsyncMock()
        mock_cb = MagicMock(spec=CallbackQuery)
        mock_cb.data = "onboard_cat:python"
        mock_cb.from_user = TgUser(id=870003, is_bot=False, first_name="Py", username="py_dev")
        mock_cb.bot = mock_bot
        mock_cb.message = AsyncMock()
        mock_cb.answer = AsyncMock()

        await handle_onboarding_category(mock_cb)

        # 1. Category saved
        uf = await get_user_filters(870003)
        self.assertEqual(uf.categories, "python")

        # 2. Onboarding message edited into main menu with confirmation
        mock_cb.message.edit_text.assert_called_once()
        menu_text = mock_cb.message.edit_text.call_args[0][0]
        self.assertIn("🐍 Python", menu_text)
        self.assertIn("Добро пожаловать в Lead Radar", menu_text)
        mock_cb.answer.assert_called_once()

        # 3. Real lead from DB delivered with Trial 24h badge & pitch button
        mock_bot.send_message.assert_called_once()
        lead_kwargs = mock_bot.send_message.call_args.kwargs
        self.assertEqual(lead_kwargs["chat_id"], 870003)
        self.assertIn("TRIAL 24ч", lead_kwargs["text"])
        self.assertIn("Парсер маркетплейсов на Python + FastAPI", lead_kwargs["text"])

        lead_kb = lead_kwargs["reply_markup"]
        lead_btn_texts = [b.text for row in lead_kb.inline_keyboard for b in row]
        lead_btn_cbs = [b.callback_data for row in lead_kb.inline_keyboard for b in row if b.callback_data]
        self.assertIn("💡 Шаблон отклика", lead_btn_texts)
        self.assertTrue(any(cb.startswith("pitch_") for cb in lead_btn_cbs))

        # 4. Subsequent /start now renders standard main menu instead of onboarding
        msg2 = MagicMock(spec=Message)
        msg2.from_user = TgUser(id=870003, is_bot=False, first_name="Py", username="py_dev")
        msg2.text = "/start"
        msg2.answer = AsyncMock()
        msg2.bot = AsyncMock()
        await render_welcome(msg2)
        menu2_text = msg2.answer.call_args[0][0]
        self.assertIn("Статус радара", menu2_text)
        menu2_kb = msg2.answer.call_args.kwargs["reply_markup"]
        menu2_cbs = [b.callback_data for row in menu2_kb.inline_keyboard for b in row if b.callback_data]
        self.assertIn("open_help", menu2_cbs)

    async def test_onboard_cat_all_and_invalid(self):
        await get_or_create_user(870004, username="all_dev")

        # Invalid category must fail safely without unlocking 'none'
        bad_cb = MagicMock(spec=CallbackQuery)
        bad_cb.data = "onboard_cat:hacked_invalid"
        bad_cb.from_user = TgUser(id=870004, is_bot=False, first_name="All", username="all_dev")
        bad_cb.answer = AsyncMock()
        await handle_onboarding_category(bad_cb)
        bad_cb.answer.assert_called_once_with("Неизвестная специализация", show_alert=True)
        uf_still_none = await get_user_filters(870004)
        self.assertEqual(uf_still_none.categories, "none")

        # Valid 'all' choice unlocks user
        good_cb = MagicMock(spec=CallbackQuery)
        good_cb.data = "onboard_cat:all"
        good_cb.from_user = TgUser(id=870004, is_bot=False, first_name="All", username="all_dev")
        good_cb.bot = AsyncMock()
        good_cb.message = AsyncMock()
        good_cb.answer = AsyncMock()
        await handle_onboarding_category(good_cb)
        uf_all = await get_user_filters(870004)
        self.assertEqual(uf_all.categories, "all")
        good_cb.bot.send_message.assert_called_once()

    async def test_onboard_cat_empty_db_fallback_and_copywriting(self):
        future_ts = datetime.now(timezone.utc) + timedelta(days=365)
        await save_lead(
            source="fl_ru",
            external_id="onb_real_copy_1",
            title="Написание продающих текстов для сайта",
            description="Требуется опытный копирайтер для лендинга",
            url="https://www.fl.ru/projects/copy1",
            budget="15 000 ₽",
            category="copywriting",
            published_at=future_ts,
        )
        await get_or_create_user(870004, username="copy_dev")

        cb_copy = MagicMock(spec=CallbackQuery)
        cb_copy.data = "onboard_cat:copywriting"
        cb_copy.from_user = TgUser(id=870004, is_bot=False, first_name="Copy", username="copy_dev")
        cb_copy.bot = AsyncMock()
        cb_copy.message = AsyncMock()
        cb_copy.answer = AsyncMock()

        await handle_onboarding_category(cb_copy)
        uf = await get_user_filters(870004)
        self.assertEqual(uf.categories, "copywriting")
        sent_text = cb_copy.bot.send_message.call_args.kwargs["text"]
        self.assertIn("Написание продающих текстов для сайта", sent_text)

        # Fallback when DB has no matching leads
        cb_empty = MagicMock(spec=CallbackQuery)
        cb_empty.data = "onboard_cat:mobile"
        cb_empty.from_user = TgUser(id=870004, is_bot=False, first_name="Mob", username="copy_dev")
        cb_empty.bot = AsyncMock()
        cb_empty.message = AsyncMock()
        cb_empty.answer = AsyncMock()

        with patch("handlers.start.get_recent_leads", new=AsyncMock(return_value=[])):
            await handle_onboarding_category(cb_empty)

        cb_empty.bot.send_message.assert_called_once()
        fallback_text = cb_empty.bot.send_message.call_args.kwargs["text"]
        self.assertIn("Flutter", fallback_text)
        self.assertIn("TRIAL 24ч", fallback_text)

    async def test_filters_lift_none_on_keyword_save(self):
        await get_or_create_user(870001, username="kw_first_user")
        uf_before = await get_user_filters(870001)
        self.assertEqual(uf_before.categories, "none")

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=870001, is_bot=False, first_name="Kw")
        msg.text = "python, fastapi"
        msg.answer = AsyncMock()
        msg.bot = AsyncMock()
        state = AsyncMock()

        await process_keywords(msg, state)
        uf_after = await get_user_filters(870001)
        self.assertEqual(uf_after.keywords, "python, fastapi")
        self.assertEqual(uf_after.categories, "all")
        self.assertTrue(matches_filter("нужен fastapi разработчик", uf_after.keywords, categories_str=uf_after.categories))


class TestHelpAndBotProfile(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.test_uid = 870010
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id == self.test_uid))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id == self.test_uid))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id == self.test_uid))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id == self.test_uid))
            await s.commit()
        await engine.dispose()

    async def test_help_command_and_callback(self):
        text = build_help_text()
        self.assertIn("@PremiumBot", text)
        self.assertIn("fragment.com", text.lower())
        self.assertIn("Apple Pay", text)
        self.assertIn("Шаблон отклика", text)
        self.assertIn("Поддержка", text)

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=self.test_uid, is_bot=False, first_name="Helper")
        msg.text = "/help"
        msg.answer = AsyncMock()
        await show_help(msg)
        msg.answer.assert_called_once()
        self.assertIn("Как купить Telegram Stars", msg.answer.call_args[0][0])

        cb = MagicMock(spec=CallbackQuery)
        cb.data = "open_help"
        cb.from_user = TgUser(id=self.test_uid, is_bot=False, first_name="Helper")
        cb.message = AsyncMock()
        cb.answer = AsyncMock()
        await show_help(cb)
        cb.message.edit_text.assert_called_once()
        cb.answer.assert_called_once()

    async def test_profile_contains_help_button(self):
        await get_or_create_user(self.test_uid, username="prof_user")
        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "fast_leadss_bot"
        mock_bot.get_me = AsyncMock(return_value=mock_me)

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=self.test_uid, is_bot=False, first_name="Prof")
        msg.bot = mock_bot
        msg.answer = AsyncMock()
        await show_profile(msg)
        kb = msg.answer.call_args.kwargs["reply_markup"]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
        self.assertIn("open_help", cbs)

    async def test_bot_commands_and_descriptions_limits(self):
        cmd_names = [c.command for c in BOT_COMMANDS]
        self.assertEqual(cmd_names, ["start", "filters", "test_lead", "premium", "profile", "help"])
        self.assertLessEqual(len(BOT_DESCRIPTION), 512)
        self.assertLessEqual(len(BOT_SHORT_DESCRIPTION), 120)
        self.assertIn("Welcome Trial", BOT_DESCRIPTION)
        self.assertIn("отклик", BOT_DESCRIPTION.lower())
        self.assertIn("Welcome Trial", BOT_SHORT_DESCRIPTION)
        self.assertIn("отклик", BOT_SHORT_DESCRIPTION.lower())

        mock_bot = AsyncMock()
        with patch.object(settings, "WEBAPP_URL", "https://example.up.railway.app"):
            await configure_bot_profile(mock_bot)

        mock_bot.set_my_commands.assert_called_once_with(BOT_COMMANDS)
        mock_bot.set_my_description.assert_called_once_with(description=BOT_DESCRIPTION)
        mock_bot.set_my_short_description.assert_called_once_with(short_description=BOT_SHORT_DESCRIPTION)
        mock_bot.set_chat_menu_button.assert_called_once()


class TestPaymentAdminNotifications(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.test_uids = [870020, 870021, 870022]
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_uids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_uids)))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_uids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_uids)))
            await s.commit()
        await engine.dispose()

    async def test_successful_payment_notifies_all_admins(self):
        await get_or_create_user(870020, username="buyer_dev", utm_source="utm_tg_ads")
        # Simulate 2 referrals via invitees
        await get_or_create_user(870021, username="inv1", referred_by=870020)
        await get_or_create_user(870022, username="inv2", referred_by=870020)

        mock_bot = AsyncMock()
        payment = MagicMock(spec=SuccessfulPayment)
        payment.total_amount = 250

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=870020, is_bot=False, first_name="Buyer", username="buyer_dev")
        msg.successful_payment = payment
        msg.answer = AsyncMock()
        msg.bot = mock_bot

        with patch.object(settings, "ADMIN_IDS", "1001, 1002"):
            await process_successful_payment(msg)

        msg.answer.assert_called_once()
        self.assertIn("Premium активирован", msg.answer.call_args[0][0])

        self.assertEqual(mock_bot.send_message.call_count, 2)
        calls_by_chat = {
            c.kwargs["chat_id"]: c.kwargs for c in mock_bot.send_message.call_args_list
        }
        self.assertEqual(set(calls_by_chat.keys()), {1001, 1002})
        expected_text = (
            "💰 НОВАЯ ОПЛАТА: 250 Stars от @buyer_dev (ID: 870020) "
            "| Источник: utm_tg_ads | Рефералов: 2"
        )
        self.assertEqual(calls_by_chat[1001]["text"], expected_text)
        self.assertTrue(calls_by_chat[1001]["disable_notification"])

    async def test_successful_payment_handles_missing_username_and_partial_admin_error(self):
        await get_or_create_user(870020, username=None, utm_source=None)

        mock_bot = AsyncMock()
        # First admin fails, second succeeds
        mock_bot.send_message.side_effect = [RuntimeError("Bot blocked"), None]

        payment = MagicMock(spec=SuccessfulPayment)
        payment.total_amount = 250

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=870020, is_bot=False, first_name="Anon", username=None)
        msg.successful_payment = payment
        msg.answer = AsyncMock()
        msg.bot = mock_bot

        with patch.object(settings, "ADMIN_IDS", "1001, 1002"):
            await process_successful_payment(msg)

        msg.answer.assert_called_once()
        self.assertEqual(mock_bot.send_message.call_count, 2)
        second_text = mock_bot.send_message.call_args_list[1].kwargs["text"]
        self.assertIn("@no_username", second_text)
        self.assertIn("Источник: organic", second_text)
        self.assertIn("Рефералов: 0", second_text)


class TestWebAppHygieneAndHtml(AioHTTPTestCase):
    async def get_application(self):
        await init_db()
        return create_webapp(bot=AsyncMock())

    async def test_health_endpoints(self):
        for route in ("/health", "/api/health"):
            resp = await self.client.get(route)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data, {"ok": True, "status": "healthy"})

    async def test_robots_txt_and_favicon(self):
        robots = await self.client.get("/robots.txt")
        self.assertEqual(robots.status, 200)
        body = await robots.text()
        self.assertEqual(body, "User-agent: *\nDisallow: /api/")

        fav = await self.client.get("/favicon.ico")
        self.assertEqual(fav.status, 204)

    async def test_webapp_html_removed_habr_and_added_tg_channels(self):
        html_text = (PROJECT_ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Хабр Фриланс", html_text)
        self.assertIn("Telegram-каналы заказов", html_text)
        self.assertIn("Мониторинг чатов и каналов", html_text)


class TestHardenedEdgeCases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.test_uids = [870030, 870031, 870032]
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_uids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_uids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_uids)))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_uids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_uids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_uids)))
            await s.commit()
        await engine.dispose()

    def test_ensure_sqlite_parent_dir_skips_file_mode_memory(self):
        with patch.object(Path, "mkdir") as mock_mkdir:
            ensure_sqlite_parent_dir("sqlite+aiosqlite:///file:/nonexistent_mem_dir/db?mode=memory&cache=shared")
            mock_mkdir.assert_not_called()

    async def test_back_to_menu_keeps_unonboarded_user_on_onboarding(self):
        await get_or_create_user(870030, username="unonb_cb")

        cb = MagicMock(spec=CallbackQuery)
        cb.data = "back_to_menu"
        cb.from_user = TgUser(id=870030, is_bot=False, first_name="Unonb", username="unonb_cb")
        cb.bot = AsyncMock()
        cb.message = AsyncMock()
        cb.answer = AsyncMock()

        await back_to_menu_callback(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        self.assertIn("Выберите вашу специализацию", text)
        self.assertNotIn("лиды приходят", text)
        cb.answer.assert_called_once()

    async def test_filters_html_escaping_non_text_fsm_and_single_callback_answer(self):
        await get_or_create_user(870031, username="filt_user")
        await update_user_full_filters(870031, keywords="c# <script>", stop_words="a&b", categories="backend")

        rendered = await render_filters_text(870031)
        self.assertIn("c# &lt;script&gt;", rendered)
        self.assertIn("a&amp;b", rendered)

        # Non-text FSM messages must not crash with AttributeError
        non_text_msg = MagicMock(spec=Message)
        non_text_msg.from_user = TgUser(id=870031, is_bot=False, first_name="Filt")
        non_text_msg.text = None
        non_text_msg.answer = AsyncMock()
        state = AsyncMock()
        await process_keywords(non_text_msg, state)
        await process_stop_words(non_text_msg, state)
        self.assertEqual(non_text_msg.answer.call_count, 2)
        state.clear.assert_not_called()

        # Reset filters must answer callback once and handle unmodified edit_text safely
        cb_reset = MagicMock(spec=CallbackQuery)
        cb_reset.data = "reset_filters"
        cb_reset.from_user = TgUser(id=870031, is_bot=False, first_name="Filt")
        cb_reset.message = AsyncMock()
        cb_reset.message.edit_text = AsyncMock(side_effect=RuntimeError("Bad Request: message is not modified"))
        cb_reset.answer = AsyncMock()
        await reset_filters_handler(cb_reset)
        cb_reset.answer.assert_called_once_with("🔄 Фильтры сброшены", show_alert=True)
        cb_reset.message.answer.assert_not_called()

    async def test_toggle_leads_from_profile_answers_callback_once(self):
        await get_or_create_user(870032, username="prof_toggle")
        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "fast_leadss_bot"
        mock_bot.get_me = AsyncMock(return_value=mock_me)

        cb = MagicMock(spec=CallbackQuery)
        cb.data = "toggle_leads"
        cb.from_user = TgUser(id=870032, is_bot=False, first_name="Prof", username="prof_toggle")
        cb.bot = mock_bot
        cb.message = AsyncMock()
        cb.message.text = "👤 Профиль фрилансера"
        cb.answer = AsyncMock()

        await toggle_leads_callback(cb)
        cb.answer.assert_called_once()
        cb.message.edit_text.assert_called_once()

    async def test_admin_payment_alert_uses_plain_text_parse_mode_none(self):
        await get_or_create_user(870032, username="html_utm_buyer", utm_source="utm_<bad>&source")

        mock_bot = AsyncMock()
        payment = MagicMock(spec=SuccessfulPayment)
        payment.total_amount = 250

        msg = MagicMock(spec=Message)
        msg.from_user = TgUser(id=870032, is_bot=False, first_name="Buyer", username="html_utm_buyer")
        msg.successful_payment = payment
        msg.answer = AsyncMock()
        msg.bot = mock_bot

        with patch.object(settings, "ADMIN_IDS", "1001"):
            await process_successful_payment(msg)

        mock_bot.send_message.assert_called_once()
        kwargs = mock_bot.send_message.call_args.kwargs
        self.assertIsNone(kwargs["parse_mode"])
        self.assertIn("utm_<bad>&source", kwargs["text"])


if __name__ == "__main__":
    unittest.main()
