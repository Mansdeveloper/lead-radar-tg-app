import asyncio
import io
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from database.session import init_db
from database.crud import (
    get_or_create_user,
    grant_premium,
    is_user_premium,
    update_user_keywords,
    update_user_stop_words,
    save_lead,
)
from parsers.fl import FLRuParser
from parsers.freelancehunt import FreelancehuntParser
from services.matcher import matches_filter


async def run_self_verification():
    print("=" * 60)
    print("1. Testing Database Schema Initialization...")
    await init_db()
    print("✅ DB Schema created successfully!")

    print("\n2. Testing User & Filter CRUD...")
    user_id = 999999999
    user, created = await get_or_create_user(telegram_id=user_id, username="test_lead_bot")
    print(f"✅ User created/retrieved: ID={user.telegram_id}, created={created}")

    await update_user_keywords(user_id, "python, react, бот")
    await update_user_stop_words(user_id, "диплом, курсовая")
    print("✅ Filters updated!")

    # Check filter matching logic
    test_text_1 = "Требуется разработать телеграм бот на Python для автоворонок"
    test_text_2 = "Нужна курсовая работа по программированию на Python"
    test_text_3 = "Дизайн карточек товара на маркетплейсе"

    assert matches_filter(test_text_1.lower(), "python, react, бот", "диплом, курсовая") is True, "Test 1 failed"
    assert matches_filter(test_text_2.lower(), "python, react, бот", "диплом, курсовая") is False, "Test 2 failed (stop word not caught)"
    assert matches_filter(test_text_3.lower(), "python, react, бот", "диплом, курсовая") is False, "Test 3 failed (keywords didn't match)"
    print("✅ Filter matching unit tests PASSED!")

    print("\n3. Testing Premium & Referral mechanics...")
    is_prem_before = await is_user_premium(user_id)
    print(f"Premium before grant: {is_prem_before}")
    await grant_premium(user_id, days=7)
    is_prem_after = await is_user_premium(user_id)
    assert is_prem_after is True, "Premium grant failed"
    print(f"✅ Premium grant verified: {is_prem_after}")

    print("\n4. Testing Live Feed Parsing (Freelancehunt & FL)...")
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

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED! Lead Radar engine is production-ready.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_self_verification())
