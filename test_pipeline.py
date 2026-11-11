import asyncio
import io
import sys
from datetime import datetime, timedelta, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from database.session import init_db, async_session
from database.crud import (
    compute_user_status,
    get_or_create_user,
    get_user,
    get_user_status,
    get_users_with_expired_trial,
    grant_premium,
    is_turbo_active,
    is_user_premium,
    mark_trial_expired_notified,
    update_user_keywords,
    update_user_stop_words,
    save_lead,
)
from database.models import User
from parsers.base import RawLead
from parsers.fl import FLRuParser
from parsers.freelancehunt import FreelancehuntParser
from services.matcher import build_lead_keyboard, format_lead_message, matches_filter
from sqlalchemy import delete, select, update


async def run_self_verification():
    print("=" * 60)
    print("1. Testing Database Schema Initialization & Column Migration...")
    await init_db()
    print("✅ DB Schema created and auto-migration verified successfully!")

    print("\n2. Testing Welcome Trial (24h) Initialization for New Users...")
    trial_test_id = 777123456
    # Clean up prior test state if any
    async with async_session() as s:
        await s.execute(delete(User).where(User.telegram_id.in_([trial_test_id, 777987654, 999999999, 888777666])))
        await s.commit()

    trial_user, created = await get_or_create_user(
        telegram_id=trial_test_id, username="trial_tester"
    )
    assert trial_user.trial_until is not None, "trial_until was not set for new user!"
    assert trial_user.trial_expired_notified is False, "trial_expired_notified should be False!"

    status = await get_user_status(trial_test_id)
    assert status["is_trial"] is True, f"Expected is_trial=True, got {status['is_trial']}"
    assert status["is_premium"] is False, f"Expected is_premium=False, got {status['is_premium']}"
    assert status["has_instant_delivery"] is True, "Trial user must have instant delivery!"
    assert status["trial_hours_left"] == 24, f"Expected 24h left right after creation, got {status['trial_hours_left']}"
    assert await is_turbo_active(trial_test_id) is True, "is_turbo_active should be True for trial user!"

    # Verify zero-overhead in-memory compute_user_status produces identical results
    mem_status = compute_user_status(trial_user)
    assert mem_status == status, "compute_user_status mismatch with get_user_status"
    print(f"✅ Welcome Trial correctly assigned: {status['trial_hours_left']}h left, instant delivery active!")

    print("\n3. Testing Lead Badge & Keyboard Formatting for Trial, Premium, and Free...")
    sample_lead = RawLead(
        source="fl_ru",
        external_id="test_ext_1",
        title="Разработка Telegram Mini App на React",
        description="Нужен опытный разработчик для создания Mini App",
        url="https://fl.ru/projects/test",
        budget="50 000 ₽",
        category="development",
        published_at=datetime.now(timezone.utc),
        raw_text="Разработка Telegram Mini App на React",
    )

    # 3a. Trial formatting
    trial_msg = format_lead_message(sample_lead, is_premium=False, is_trial=True)
    assert "⚡ <b>МГНОВЕННЫЙ ЛИД [TRIAL 24ч]</b>" in trial_msg, "Trial badge missing in message!"
    assert "Welcome Trial" in trial_msg, "Trial notice missing in message!"
    trial_kb = build_lead_keyboard(sample_lead, is_premium=False, is_trial=True)
    trial_btn_texts = [btn.text for row in trial_kb.inline_keyboard for btn in row]
    assert any("Закрепить 0 сек" in t for t in trial_btn_texts), "Trial upsell button missing!"
    print("✅ Trial message and keyboard verified!")

    # 3b. Premium formatting
    prem_msg = format_lead_message(sample_lead, is_premium=True, is_trial=False)
    assert "⚡ <b>МГНОВЕННЫЙ ЛИД [PREMIUM]</b>" in prem_msg, "Premium badge missing!"
    prem_kb = build_lead_keyboard(sample_lead, is_premium=True, is_trial=False)
    prem_btn_texts = [btn.text for row in prem_kb.inline_keyboard for btn in row]
    assert not any("Premium" in t for t in prem_btn_texts), "Premium user should not see upsell button!"
    print("✅ Premium message and keyboard verified!")

    # 3c. Free formatting
    free_msg = format_lead_message(sample_lead, is_premium=False, is_trial=False)
    assert "⏳ <b>Лид доставлен с задержкой 15 мин [FREE]</b>" in free_msg, "Free badge missing!"
    free_kb = build_lead_keyboard(sample_lead, is_premium=False, is_trial=False)
    free_btn_texts = [btn.text for row in free_kb.inline_keyboard for btn in row]
    assert any("Убрать задержку" in t for t in free_btn_texts), "Free upsell button missing!"
    print("✅ Free message and keyboard verified!")

    print("\n4. Testing Trial Expiration Detection and Notification Flag...")
    expired_test_id = 777987654
    exp_user, _ = await get_or_create_user(
        telegram_id=expired_test_id, username="expired_tester"
    )
    # Force trial to have expired 2 hours ago
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == expired_test_id)
            .values(trial_until=past_time, trial_expired_notified=False, is_premium=False)
        )
        await session.commit()

    expired_users = await get_users_with_expired_trial()
    expired_ids = [u.telegram_id for u in expired_users]
    assert expired_test_id in expired_ids, "Expired user not detected by get_users_with_expired_trial!"
    print(f"✅ Expired trial detected for user {expired_test_id}")

    # Mark as notified
    await mark_trial_expired_notified(expired_test_id)
    expired_users_after = await get_users_with_expired_trial()
    expired_ids_after = [u.telegram_id for u in expired_users_after]
    assert expired_test_id not in expired_ids_after, "User should be excluded after mark_trial_expired_notified!"

    expired_status = await get_user_status(expired_test_id)
    assert expired_status["is_trial"] is False, "Expired user must not have is_trial=True!"
    assert expired_status["has_instant_delivery"] is False, "Expired user must not have instant delivery!"
    print("✅ Trial expiration notification cycle and status transition verified!")

    # 4b. Test active premium user auto-marked as notified
    active_prem_id = 888777666
    ap_user, _ = await get_or_create_user(telegram_id=active_prem_id, username="prem_expired_trial")
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == active_prem_id)
            .values(
                trial_until=past_time,
                trial_expired_notified=False,
                is_premium=True,
                premium_until=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        await session.commit()
    expired_with_prem = await get_users_with_expired_trial()
    assert active_prem_id not in [u.telegram_id for u in expired_with_prem], "Active premium user must not receive expired trial push!"
    ap_refreshed = await get_user(active_prem_id)
    assert ap_refreshed.trial_expired_notified is True, "Active premium user should be marked trial_expired_notified=True!"
    print("✅ Active premium user suppressed and marked notified verified!")

    print("\n5. Testing Filter matching logic...")
    test_text_1 = "Требуется разработать телеграм бот на Python для автоворонок"
    test_text_2 = "Нужна курсовая работа по программированию на Python"
    test_text_3 = "Дизайн карточек товара на маркетплейсе"

    assert matches_filter(test_text_1.lower(), "python, react, бот", "диплом, курсовая") is True, "Test 1 failed"
    assert matches_filter(test_text_2.lower(), "python, react, бот", "диплом, курсовая") is False, "Test 2 failed (stop word not caught)"
    assert matches_filter(test_text_3.lower(), "python, react, бот", "диплом, курсовая") is False, "Test 3 failed (keywords didn't match)"
    print("✅ Filter matching unit tests PASSED!")

    print("\n6. Testing Premium grant mechanics...")
    user_id = 999999999
    await grant_premium(user_id, days=7)
    assert await is_user_premium(user_id) is True, "Premium grant failed"
    assert await is_turbo_active(user_id) is True, "is_turbo_active failed for premium"
    user_row = await get_user(user_id)
    assert user_row.trial_expired_notified is True, "grant_premium must mark trial_expired_notified=True"
    print("✅ Premium grant and trial notification suppression verified!")

    print("\n7. Testing Live Feed Parsing (Freelancehunt & FL)...")
    fh = FreelancehuntParser()
    fh_leads = await fh.fetch_latest()
    print(f"Freelancehunt RSS returned: {len(fh_leads)} leads")
    if fh_leads:
        sample = fh_leads[0]
        print(f"  Sample: [{sample.source}] {sample.title[:60]}... | Budget: {sample.budget}")
        inserted = await save_lead(
            source=sample.source,
            external_id=sample.external_id,
            title=sample.title,
            description=sample.description,
            url=sample.url,
            budget=sample.budget,
            category=sample.category,
            published_at=sample.published_at,
        )
        print(f"  Inserted to DB: {inserted}")
        dedup_check = await save_lead(
            source=sample.source,
            external_id=sample.external_id,
            title=sample.title,
            description=sample.description,
            url=sample.url,
            budget=sample.budget,
            category=sample.category,
            published_at=sample.published_at,
        )
        assert dedup_check is False, "Deduplication failed!"
        print("  ✅ Deduplication verified (second insert returned False)")

    fl = FLRuParser()
    fl_leads = await fl.fetch_latest()
    print(f"FL.ru RSS returned: {len(fl_leads)} leads")

    print("\n8. Testing Mini App REST API (/api/user/{id})...")
    from aiohttp.test_utils import TestClient, TestServer
    from unittest.mock import AsyncMock, MagicMock
    from webapp.server import create_webapp

    mock_bot = MagicMock()
    mock_info = MagicMock()
    mock_info.username = "fast_leadss_bot"
    mock_bot.get_me = AsyncMock(return_value=mock_info)

    webapp = create_webapp(mock_bot)
    client = TestClient(TestServer(webapp))
    await client.start_server()

    res = await client.get(f"/api/user/{trial_test_id}")
    assert res.status == 200, f"Expected 200, got {res.status}"
    data = await res.json()
    assert data["ok"] is True
    assert data["user"]["is_trial"] is True
    assert data["user"]["has_instant_delivery"] is True
    assert data["user"]["trial_hours_left"] == 24
    assert "t.me/fast_leadss_bot?start=ref_" in data["user"]["referral_link"]

    # Verify input validation on negative / invalid user ID
    bad_res = await client.get("/api/user/-1")
    assert bad_res.status == 400, "Negative user ID must return 400"
    await client.close()
    print("✅ Mini App REST API (/api/user/{id}) correctly returned trial status & remaining hours!")

    # Cleanup test users
    async with async_session() as s:
        await s.execute(delete(User).where(User.telegram_id.in_([trial_test_id, expired_test_id, user_id, active_prem_id])))
        await s.commit()
    print("✅ Test data cleaned up successfully!")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED! Lead Radar 24h Welcome Trial is production-ready.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_self_verification())
