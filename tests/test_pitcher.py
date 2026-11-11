import asyncio
import io
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiogram.types import CallbackQuery, Message, User as TgUser

from sqlalchemy import delete

from database.models import Lead, User, UserFilter
from database.session import async_session, init_db
from database.crud import (
    get_lead_by_external_id,
    get_lead_by_id,
    get_or_create_user,
    get_recent_leads,
    get_user_filters,
    get_user_status,
    save_lead,
    update_user_full_filters,
)
from handlers.start import cmd_test_lead, handle_pitch_callback, send_test_lead
from parsers.base import RawLead
from services.matcher import build_lead_keyboard, format_lead_message
from services.pitcher import (
    CATEGORY_EXPERIENCE,
    CATEGORY_QUESTIONS,
    _clean_task_essence,
    generate_pitch,
)
from webapp.server import create_webapp


class TestPitchGenerator(unittest.TestCase):
    def test_clean_task_essence_noise_removal(self):
        cases = [
            ("Требуется разработка Telegram-бота под ключ", "Telegram-бота под ключ"),
            ("Нужен опытный разработчик Python для парсинга", "опытный разработчик Python для парсинга"),
            ("Срочно разработать лендинг на React!", "лендинг на React"),
            ("Создание логотипа и брендбука для онлайн-магазина.", "логотипа и брендбука для онлайн-магазина"),
            ("Ищу специалиста по настройке 1С", "специалиста по настройке 1С"),
            ("Проект: Настройка рекламы в Яндекс Директ", "Настройка рекламы в Яндекс Директ"),
        ]
        for title, expected_snippet in cases:
            cleaned = _clean_task_essence(title)
            self.assertIn(expected_snippet.lower(), cleaned.lower())

    def test_clean_task_essence_empty_fallback(self):
        self.assertEqual(_clean_task_essence(""), "реализовать данный проект")
        self.assertEqual(_clean_task_essence(None), "реализовать данный проект")
        self.assertEqual(_clean_task_essence("   ", "Описание задачи"), "Описание задачи")

    def test_clean_task_essence_long_title_truncation(self):
        long_title = "Нужно создать очень длинный и сложный проект с микросервисной архитектурой, интеграцией с базами данных, очередями RabbitMQ, Redis, Kubernetes и мобильным клиентом"
        cleaned = _clean_task_essence(long_title)
        self.assertLessEqual(len(cleaned), 90)

    def test_generate_pitch_formula_structure(self):
        title = "Разработка Telegram-бота для службы доставки"
        pitch = generate_pitch(title=title, category="bot")

        # Must have:
        # Sentence 1: Confirmation
        # Sentence 2: Experience
        # Sentence 3: Question
        self.assertTrue(pitch.startswith("Здравствуйте! Внимательно ознакомился с задачей: разработка Telegram-бота"))
        self.assertIn("Telegram-бот", pitch)
        self.assertTrue(pitch.endswith("?"))

        # Check that question and experience exist
        self.assertIn(CATEGORY_QUESTIONS["bot"], pitch)
        self.assertIn(CATEGORY_EXPERIENCE["bot"], pitch)

    def test_generate_pitch_grammar_and_casing(self):
        # Verify that common freelance titles don't result in ungrammatical phrases
        cases = [
            ("Настройка контекстной рекламы в Яндекс Директ", "настройка контекстной рекламы"),
            ("Срочно требуется доработка сайта на Bitrix", "доработка сайта на Bitrix"),
            ("Разработка Telegram-бота под ключ", "разработка Telegram-бота"),
        ]
        for title, expected_sub in cases:
            p = generate_pitch(title=title)
            self.assertIn(expected_sub, p)
            self.assertNotIn("— нужно сайта", p)
            self.assertNotIn("— нужно рекламы", p)
            self.assertNotIn("— нужно бота", p)

    def test_generate_pitch_categories(self):
        categories = ["bot", "python", "frontend", "backend", "mobile", "design", "1c", "marketing", "copywriting", "analytics", "devops", "gamedev", "other"]
        for cat in categories:
            pitch = generate_pitch(title="Задача по стеку", category=cat)
            self.assertIn(CATEGORY_EXPERIENCE.get(cat, CATEGORY_EXPERIENCE["other"]), pitch)
            self.assertIn(CATEGORY_QUESTIONS.get(cat, CATEGORY_QUESTIONS["other"]), pitch)

    def test_generate_pitch_auto_detect_category(self):
        # Auto detect from title
        pitch_py = generate_pitch(title="Написать быстрый парсер на Python для каталога товаров")
        self.assertIn(CATEGORY_EXPERIENCE["python"], pitch_py)

        pitch_figma = generate_pitch(title="Отрисовать дизайн интерфейса в Figma для приложения")
        self.assertIn(CATEGORY_EXPERIENCE["design"], pitch_figma)

        pitch_tg = generate_pitch(title="Сделать чат-бота в Telegram с админкой")
        self.assertIn(CATEGORY_EXPERIENCE["bot"], pitch_tg)


class TestLeadKeyboardAndPitchIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    async def test_build_lead_keyboard_has_pitch_button(self):
        lead = RawLead(
            source="fl_ru",
            external_id="test_ext_kbd",
            title="Тестовый заказ",
            description="Описание",
            url="https://fl.ru/test",
            budget="50 000 ₽",
            category="bot",
            published_at=datetime.now(timezone.utc),
            raw_text="тестовый заказ",
            id=1234,
        )
        kb = build_lead_keyboard(lead, is_premium=False, is_trial=True)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        btn_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]

        self.assertIn("💡 Шаблон отклика", btn_texts)
        self.assertIn("pitch_1234", btn_cbs)

    async def test_build_lead_keyboard_fallback_when_no_id(self):
        lead = RawLead(
            source="fl_ru",
            external_id="fh_sample_12",
            title="Тестовый заказ",
            description="Описание",
            url="https://fl.ru/test",
            budget="50 000 ₽",
            category="bot",
            published_at=datetime.now(timezone.utc),
            raw_text="тестовый заказ",
            id=None,
        )
        kb = build_lead_keyboard(lead, is_premium=True)
        btn_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("pitch_fh_sample_12", btn_cbs)

    async def test_send_test_lead_execution(self):
        test_uid = 998877665
        await get_or_create_user(telegram_id=test_uid, username="tester_radar")
        await update_user_full_filters(
            telegram_id=test_uid,
            keywords="парсер",
            stop_words="",
            min_budget=15000,
            categories="python",
        )

        mock_bot = AsyncMock()
        mock_msg = MagicMock(spec=Message)
        mock_msg.from_user = TgUser(id=test_uid, is_bot=False, first_name="Tester", username="tester_radar")
        mock_msg.bot = mock_bot
        mock_msg.text = "/test_lead"

        await send_test_lead(mock_msg)

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        self.assertEqual(call_kwargs["chat_id"], test_uid)
        self.assertIn("ТЕСТОВЫЙ ЛИД", call_kwargs["text"])
        self.assertIn("TRIAL", call_kwargs["text"])

        kb = call_kwargs["reply_markup"]
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("💡 Шаблон отклика", btn_texts)

    async def test_handle_pitch_callback(self):
        # Save a lead in DB to test callback
        ext_id = "test_lead_pitch_cb_1"
        await save_lead(
            source="fl_ru",
            external_id=ext_id,
            title="Разработка скрипта на Python",
            description="Скрипт для парсинга и обработки данных",
            url="https://fl.ru/test/1",
            budget="20 000 ₽",
            category="python",
            published_at=datetime.now(timezone.utc),
        )
        lead = await get_lead_by_external_id(ext_id)
        self.assertIsNotNone(lead)

        mock_cb = AsyncMock()
        mock_cb.data = f"pitch_{lead.id}"
        mock_cb.from_user = TgUser(id=11223344, is_bot=False, first_name="Test")
        mock_cb.message = AsyncMock()
        mock_cb.answer = AsyncMock()

        await handle_pitch_callback(mock_cb)

        mock_cb.message.reply.assert_called_once()
        reply_text = mock_cb.message.reply.call_args[0][0]
        self.assertIn("Готовый шаблон отклика под этот заказ", reply_text)
        self.assertIn("<code>", reply_text)
        self.assertIn("</code>", reply_text)
        self.assertIn(CATEGORY_EXPERIENCE["python"], reply_text)
        mock_cb.answer.assert_called_once_with("✅ Формируем отклик...")

    async def test_send_test_lead_all_specialties(self):
        # Verify 1c, devops, marketing, analytics produce matching leads instead of falling back to bot
        specialties = {
            "1c": "1С",
            "devops": "CI/CD",
            "marketing": "контекстной рекламы",
            "analytics": "Power BI",
            "gamedev": "игровой",
        }
        for cat, marker in specialties.items():
            test_uid = 880000 + hash(cat) % 10000
            await get_or_create_user(telegram_id=test_uid, username=f"user_{cat}")
            await update_user_full_filters(
                telegram_id=test_uid,
                keywords="",
                stop_words="",
                min_budget=10000,
                categories=cat,
            )

            mock_bot = AsyncMock()
            mock_msg = MagicMock(spec=Message)
            mock_msg.from_user = TgUser(id=test_uid, is_bot=False, first_name="Test")
            mock_msg.bot = mock_bot
            mock_msg.text = "/test_lead"

            await send_test_lead(mock_msg)
            mock_bot.send_message.assert_called_once()
            call_text = mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn(marker.lower(), call_text.lower(), f"Lead for category {cat} should contain {marker}")

    async def test_synthetic_test_leads_excluded_from_public_feed(self):
        # Insert a synthetic test lead with test_ prefix
        test_ext = "test_user_feed_isolation_1"
        async with async_session() as s:
            await s.execute(delete(Lead).where(Lead.external_id == test_ext))
            await s.commit()

        try:
            await save_lead(
                source="fl_ru",
                external_id=test_ext,
                title="Синтетический тестовый заказ",
                description="Секретный тест",
                url="https://fl.ru/projects/sample",
                budget="100 000 ₽",
                category="bot",
                published_at=datetime.now(timezone.utc),
            )

            leads = await get_recent_leads(limit=50, include_test=False)
            self.assertNotIn(test_ext, [l.external_id for l in leads], "Synthetic test leads must not be in public feed")

            leads_with_test = await get_recent_leads(limit=50, include_test=True)
            self.assertIn(test_ext, [l.external_id for l in leads_with_test], "include_test=True must return test leads")
        finally:
            async with async_session() as s:
                await s.execute(delete(Lead).where(Lead.external_id == test_ext))
                await s.commit()


class TestWebAppPitchAPI(AioHTTPTestCase):
    async def get_application(self):
        await init_db()
        bot_mock = AsyncMock()
        return create_webapp(bot=bot_mock)

    async def test_get_lead_pitch_endpoint(self):
        ext_id = f"test_api_pitch_{int(datetime.now(timezone.utc).timestamp())}"
        await save_lead(
            source="fl_ru",
            external_id=ext_id,
            title="Разработка мобильного приложения на Flutter",
            description="Приложение под iOS и Android",
            url="https://fl.ru/test/app",
            budget="80 000 ₽",
            category="mobile",
            published_at=datetime.now(timezone.utc),
        )
        lead = await get_lead_by_external_id(ext_id)
        self.assertIsNotNone(lead)

        resp = await self.client.request("GET", f"/api/lead/{lead.id}/pitch")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("lead_id"), lead.id)
        self.assertIn(CATEGORY_EXPERIENCE["mobile"], data.get("pitch"))
        self.assertIn(CATEGORY_QUESTIONS["mobile"], data.get("pitch"))

    async def test_get_lead_pitch_not_found(self):
        resp = await self.client.request("GET", "/api/lead/99999999/pitch")
        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertFalse(data.get("ok"))


if __name__ == "__main__":
    unittest.main()
