import asyncio
import html
import io
import os
import sys
import time
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

from aiohttp.test_utils import TestClient, TestServer
from aiogram.types import Message, User as TgUser
from sqlalchemy import delete, select

from config import settings
from database.crud import (
    compute_user_status,
    get_all_subscribers_with_filters,
    get_or_create_user,
    get_user,
    get_user_filters,
    get_user_status,
    get_utm_analytics,
    grant_premium,
    record_lead_click,
    save_lead,
    toggle_user_active,
    update_user_full_filters,
)
from database.models import Achievement, Lead, User, UserFilter
from database.session import async_session, engine, init_db
from handlers.start import cmd_analytics, render_welcome
from parsers.base import RawLead
from services.matcher import dispatch_lead, matches_user_filter
from webapp.server import create_webapp


class TestUtmTrackingAndAnalytics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.test_ids = [880001, 880002, 880003, 880004, 880005, 880006]
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_ids)))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_ids)))
            await s.commit()
        await engine.dispose()

    async def test_utm_source_saved_on_user_creation(self):
        # 1. UTM source via /start utm_habr
        msg1 = MagicMock(spec=Message)
        msg1.from_user = TgUser(id=880001, is_bot=False, first_name="HabrUser", username="habr_dev")
        msg1.text = "/start utm_habr"
        msg1.answer = AsyncMock()
        msg1.bot = AsyncMock()

        await render_welcome(msg1)
        u1 = await get_user(880001)
        self.assertIsNotNone(u1)
        self.assertEqual(u1.utm_source, "utm_habr")

        # 2. UTM source via /start promo_vc
        msg2 = MagicMock(spec=Message)
        msg2.from_user = TgUser(id=880002, is_bot=False, first_name="VcUser", username="vc_dev")
        msg2.text = "/start promo_vc"
        msg2.answer = AsyncMock()
        msg2.bot = AsyncMock()

        await render_welcome(msg2)
        u2 = await get_user(880002)
        self.assertIsNotNone(u2)
        self.assertEqual(u2.utm_source, "promo_vc")

        # 3. UTM source via /start c_telegram_ads
        msg3 = MagicMock(spec=Message)
        msg3.from_user = TgUser(id=880003, is_bot=False, first_name="AdsUser", username="ads_dev")
        msg3.text = "/start c_telegram_ads"
        msg3.answer = AsyncMock()
        msg3.bot = AsyncMock()

        await render_welcome(msg3)
        u3 = await get_user(880003)
        self.assertIsNotNone(u3)
        self.assertEqual(u3.utm_source, "c_telegram_ads")

        # 4. Referral /start ref_880001
        msg4 = MagicMock(spec=Message)
        msg4.from_user = TgUser(id=880004, is_bot=False, first_name="RefUser", username="ref_dev")
        msg4.text = "/start ref_880001"
        msg4.answer = AsyncMock()
        msg4.bot = AsyncMock()

        await render_welcome(msg4)
        u4 = await get_user(880004)
        self.assertIsNotNone(u4)
        self.assertEqual(u4.referred_by, 880001)
        self.assertIsNone(u4.utm_source)

        # 5. Plain organic /start
        msg5 = MagicMock(spec=Message)
        msg5.from_user = TgUser(id=880005, is_bot=False, first_name="OrganicUser", username="organic_dev")
        msg5.text = "/start"
        msg5.answer = AsyncMock()
        msg5.bot = AsyncMock()

        await render_welcome(msg5)
        u5 = await get_user(880005)
        self.assertIsNotNone(u5)
        self.assertIsNone(u5.utm_source)

    async def test_get_utm_analytics(self):
        # Create users with specific UTM sources and statuses
        u1, _ = await get_or_create_user(880001, username="u1", utm_source="utm_habr")
        u2, _ = await get_or_create_user(880002, username="u2", utm_source="utm_habr")
        u3, _ = await get_or_create_user(880003, username="u3", utm_source="utm_vc")
        u4, _ = await get_or_create_user(880004, username="u4", utm_source=None)  # organic

        # Grant premium to u1
        await grant_premium(880001, days=30)
        # Pause u3
        await toggle_user_active(880003, is_active=False)

        analytics = await get_utm_analytics()
        self.assertIsInstance(analytics, list)
        self.assertGreaterEqual(len(analytics), 2)

        # Find utm_habr entry
        habr_entry = next((item for item in analytics if item["utm_source"] == "utm_habr"), None)
        self.assertIsNotNone(habr_entry)
        self.assertEqual(habr_entry["total_users"], 2)
        self.assertEqual(habr_entry["premium_users"], 1)
        self.assertEqual(habr_entry["trial_users"], 1)
        self.assertEqual(habr_entry["premium_or_trial"], 2)
        self.assertEqual(habr_entry["active_users"], 2)

        # Find utm_vc entry
        vc_entry = next((item for item in analytics if item["utm_source"] == "utm_vc"), None)
        self.assertIsNotNone(vc_entry)
        self.assertEqual(vc_entry["total_users"], 1)
        self.assertEqual(vc_entry["active_users"], 0)  # paused

        # Find organic entry
        organic_entry = next((item for item in analytics if item["utm_source"] == "organic"), None)
        self.assertIsNotNone(organic_entry)
        self.assertGreaterEqual(organic_entry["total_users"], 1)

    async def test_admin_stats_command(self):
        # 1. Non-admin user gets rejected
        with patch.object(settings, "ADMIN_IDS", "999999"):
            msg = MagicMock(spec=Message)
            msg.from_user = TgUser(id=880001, is_bot=False, first_name="Regular")
            msg.answer = AsyncMock()

            await cmd_analytics(msg)
            msg.answer.assert_called_once()
            self.assertIn("Доступ к статистике разрешен только администраторам", msg.answer.call_args[0][0])

        # 2. Admin user receives formatted stats
        with patch.object(settings, "ADMIN_IDS", "880001"):
            await get_or_create_user(880001, username="u1", utm_source="utm_promo_test")
            msg = MagicMock(spec=Message)
            msg.from_user = TgUser(id=880001, is_bot=False, first_name="Admin")
            msg.answer = AsyncMock()

            await cmd_analytics(msg)
            msg.answer.assert_called_once()
            out_text = msg.answer.call_args[0][0]
            self.assertIn("Сквозная аналитика UTM-источников", out_text)
            self.assertIn("utm_promo_test", out_text)
            self.assertIn("ИТОГО:", out_text)


class TestStressSystem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.base_id = 950000
        self.user_count = 120
        self.test_ids = [self.base_id + i for i in range(self.user_count)]
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_ids)))
            await s.commit()

    async def asyncTearDown(self):
        async with async_session() as s:
            await s.execute(delete(User).where(User.telegram_id.in_(self.test_ids)))
            await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_ids)))
            await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(self.test_ids)))
            await s.commit()
        await engine.dispose()

    async def test_01_concurrent_users_creation_and_filters(self):
        """Stress: 120 concurrent user registrations with diverse filters & UTM sources without DB lock."""
        print(f"\n[Stress Test] Registering {self.user_count} concurrent users with distinct filters...")
        utm_pool = ["utm_habr", "utm_vc", "utm_tg_ads", "c_facebook", "promo_special", None]
        filter_presets = [
            ("python, бот, aiogram", "wordpress, php", 10000, "bot,python"),
            ("react, vue, frontend", "дизайн", 5000, "frontend"),
            ("дизайн, figma, ui/ux", "копирайтинг", 15000, "design"),
            ("", "", 0, "all"),
            ("парсинг, scraping", "1с", 20000, "python,backend"),
        ]

        sem = asyncio.Semaphore(15)

        async def register_single(idx: int):
            async with sem:
                uid = self.base_id + idx
                utm = utm_pool[idx % len(utm_pool)]
                user, created = await get_or_create_user(
                    telegram_id=uid,
                    username=f"stress_user_{idx}",
                    utm_source=utm,
                )
                kw, sw, mb, cat = filter_presets[idx % len(filter_presets)]
                await update_user_full_filters(
                    telegram_id=uid,
                    keywords=kw,
                    stop_words=sw,
                    min_budget=mb,
                    categories=cat,
                )
                return user

        start_time = time.monotonic()
        users = await asyncio.gather(*(register_single(i) for i in range(self.user_count)))
        duration = time.monotonic() - start_time

        self.assertEqual(len(users), self.user_count)
        print(f"✅ {self.user_count} users created and configured in {duration:.2f}s (~{self.user_count/duration:.1f} users/sec)")

        # Verify all filters and users exist in DB
        async with async_session() as s:
            res_u = await s.execute(select(User).where(User.telegram_id.in_(self.test_ids)))
            saved_users = list(res_u.scalars().all())
            res_f = await s.execute(select(UserFilter).where(UserFilter.user_telegram_id.in_(self.test_ids)))
            saved_filters = list(res_f.scalars().all())

        self.assertEqual(len(saved_users), self.user_count)
        self.assertEqual(len(saved_filters), self.user_count)

    async def test_02_concurrent_read_write_no_locks(self):
        """Stress: 100 concurrent async operations (read/write/clicks/toggles) verifying zero 'database is locked' errors."""
        sub_ids = self.test_ids[:20]
        for uid in sub_ids:
            await get_or_create_user(uid, username=f"rw_{uid}")

        print("\n[Stress Test] Running 100 high-frequency concurrent read/write operations...")
        sem = asyncio.Semaphore(15)

        async def worker_task(idx: int):
            async with sem:
                target_uid = sub_ids[idx % len(sub_ids)]
                op = idx % 5
                if op == 0:
                    await record_lead_click(target_uid)
                elif op == 1:
                    await toggle_user_active(target_uid)
                elif op == 2:
                    await get_user_status(target_uid)
                elif op == 3:
                    await update_user_full_filters(
                        telegram_id=target_uid,
                        keywords=f"kw_{idx}",
                        stop_words="spam",
                        min_budget=idx * 100,
                        categories="all",
                    )
                elif op == 4:
                    await get_utm_analytics()
                return True

        start_time = time.monotonic()
        results = await asyncio.gather(*(worker_task(i) for i in range(100)), return_exceptions=True)
        duration = time.monotonic() - start_time

        exceptions = [r for r in results if isinstance(r, Exception)]
        if exceptions:
            self.fail(f"Concurrent DB operations failed with exception: {exceptions[0]}")

        print(f"✅ 100 concurrent read/write transactions succeeded in {duration:.2f}s with 0 database locks!")

    async def test_03_high_throughput_batch_matching_50_leads(self):
        """Stress: Simultaneous matching and dispatch of 50 diverse leads against 120 users."""
        print(f"\n[Stress Test] Matching batch of 50 leads against 120 users...")
        filter_presets = [
            ("python, бот, aiogram", "wordpress", 10000, "bot,python"),
            ("react, vue, frontend", "дизайн", 5000, "frontend"),
            ("дизайн, figma, ui/ux", "копирайтинг", 15000, "design"),
            ("", "", 0, "all"),
            ("парсинг, scraping", "1с", 20000, "python,backend"),
        ]
        for i in range(self.user_count):
            uid = self.base_id + i
            await get_or_create_user(uid, username=f"user_{i}")
            kw, sw, mb, cat = filter_presets[i % len(filter_presets)]
            await update_user_full_filters(uid, keywords=kw, stop_words=sw, min_budget=mb, categories=cat)

        sample_leads: list[RawLead] = []
        categories = ["bot", "python", "frontend", "design", "backend", "marketing", "other"]
        budgets = ["15 000 ₽", "3 000 ₽", "50 000 ₽", "По договоренности", "120 000 ₽"]

        for i in range(50):
            cat = categories[i % len(categories)]
            budget = budgets[i % len(budgets)]
            lead = RawLead(
                source="fl_ru" if i % 2 == 0 else "freelancehunt",
                external_id=f"stress_lead_{i}_{int(time.time())}",
                title=f"Проект #{i}: разработка {cat} решения под ключ",
                description=f"Нужен опытный специалист для проекта #{i}. Стек: {cat}, python, figma, react. Бюджет: {budget}.",
                url=f"https://example.com/lead/{i}",
                budget=budget,
                category=cat,
                published_at=datetime.now(timezone.utc),
                raw_text=f"Проект #{i}: разработка {cat} решения под ключ. Стек: {cat}, python, figma, react. Бюджет: {budget}.".lower(),
            )
            sample_leads.append(lead)

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()

        # Run 50 dispatches concurrently with mocked delayed sleeper
        with patch("services.matcher.send_delayed_lead", new_callable=AsyncMock):
            start_time = time.monotonic()
            await asyncio.gather(*(dispatch_lead(mock_bot, lead) for lead in sample_leads))
            duration = time.monotonic() - start_time

        print(f"✅ Dispatched 50 leads to 120 users in {duration:.2f}s (~{50/duration:.1f} leads/sec). Total messages dispatched: {mock_bot.send_message.call_count}")
        self.assertGreater(mock_bot.send_message.call_count, 0)

    async def test_04_webapp_api_concurrency_stress(self):
        """Stress: 100 concurrent HTTP requests across Mini App REST endpoints (TestClient)."""
        print("\n[Stress Test] Sending 100 concurrent HTTP requests to Mini App REST API...")
        mock_bot = MagicMock()
        mock_info = MagicMock()
        mock_info.username = "fast_leadss_bot"
        mock_bot.get_me = AsyncMock(return_value=mock_info)
        mock_bot.send_message = AsyncMock()

        test_uid = self.base_id + 1
        await get_or_create_user(test_uid, username="http_stress_user", utm_source="utm_api_test")

        webapp = create_webapp(mock_bot)
        client = TestClient(TestServer(webapp))
        await client.start_server()

        async def call_endpoint(idx: int):
            ep_type = idx % 4
            if ep_type == 0:
                resp = await client.get("/api/leads?limit=20&category=all")
            elif ep_type == 1:
                resp = await client.get(f"/api/user/{test_uid}")
            elif ep_type == 2:
                resp = await client.get("/api/admin/utm_stats")
            elif ep_type == 3:
                resp = await client.post(f"/api/user/{test_uid}/toggle_active")

            data = await resp.json()
            return resp.status, data.get("ok", False)

        start_time = time.monotonic()
        results = await asyncio.gather(*(call_endpoint(i) for i in range(100)))
        duration = time.monotonic() - start_time

        await client.close()

        for status, ok in results:
            self.assertEqual(status, 200)
            self.assertTrue(ok)

        print(f"✅ 100 concurrent HTTP API requests completed in {duration:.2f}s (~{100/duration:.1f} req/sec). 100% success rate!")


if __name__ == "__main__":
    unittest.main()
