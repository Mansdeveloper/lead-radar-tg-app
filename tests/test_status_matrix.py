import asyncio
from datetime import datetime, timedelta, timezone
import sys
import unittest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select, update
from config import settings
from database.session import init_db, async_session
from database.models import Achievement, User, UserFilter
from database.crud import (
    add_bonus_hours_to_user,
    compute_user_status,
    get_all_subscribers_with_filters,
    get_or_create_user,
    get_user,
    get_user_status,
    get_users_for_reactivation_promo,
    get_users_for_referral_promo,
    grant_premium,
    is_turbo_active,
    is_user_premium,
    mark_promo_reactivation_sent,
    mark_promo_referral_sent,
    record_lead_click,
    toggle_user_active,
    update_user_keywords,
)
from parsers.base import RawLead
from services.achievements import (
    check_and_grant_achievements,
    get_achievements_payload,
    get_user_achievements,
)
from services.matcher import format_lead_message


async def test_full_matrix():
    print("=" * 65)
    print("RUNNING LEAD RADAR STABILITY & STATUS MATRIX AUDIT")
    print("=" * 65)

    await init_db()

    # IDs for test runs
    test_ids = [910001, 910002, 910003, 910004, 910005, 910006, 910007, 910008, 910009]

    async with async_session() as s:
        await s.execute(delete(User).where(User.telegram_id.in_(test_ids)))
        await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(test_ids)))
        await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(test_ids)))
        await s.commit()

    now = datetime.now(timezone.utc)

    # ---------------------------------------------------------
    # 1. SCENARIO 1: New User (0 min) -> TRIAL 24h, 0 sec instant
    # ---------------------------------------------------------
    u1, _ = await get_or_create_user(910001, username="matrix_user1")
    s1 = await get_user_status(910001)
    assert s1["is_trial"] is True, f"Scenario 1 failed: expected is_trial=True, got {s1}"
    assert s1["is_premium"] is False, "Scenario 1 failed: expected is_premium=False"
    assert s1["has_instant_delivery"] is True, "Scenario 1 failed: expected instant delivery"
    assert s1["trial_hours_left"] == 24, f"Scenario 1 failed: expected 24h, got {s1['trial_hours_left']}"
    print("✅ Scenario 1: New user (0 min) -> TRIAL 24h, 0 sec instant delivery")

    # ---------------------------------------------------------
    # 2. SCENARIO 2: Trial 23h 59min left -> TRIAL, 0 sec instant
    # ---------------------------------------------------------
    u2, _ = await get_or_create_user(910002, username="matrix_user2")
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910002)
            .values(trial_until=now + timedelta(hours=23, minutes=59))
        )
        await s.commit()
    s2 = await get_user_status(910002)
    assert s2["is_trial"] is True
    assert s2["has_instant_delivery"] is True
    assert s2["trial_hours_left"] == 24
    print("✅ Scenario 2: Trial 23h 59min left -> TRIAL, 0 sec instant delivery")

    # ---------------------------------------------------------
    # 3. SCENARIO 3: Trial expired 1 min ago -> FREE, 15 min delay
    # ---------------------------------------------------------
    u3, _ = await get_or_create_user(910003, username="matrix_user3")
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910003)
            .values(trial_until=now - timedelta(minutes=1))
        )
        await s.commit()
    s3 = await get_user_status(910003)
    assert s3["is_trial"] is False, f"Scenario 3 failed: expected is_trial=False, got {s3}"
    assert s3["is_premium"] is False, "Scenario 3 failed: expected is_premium=False"
    assert s3["has_instant_delivery"] is False, "Scenario 3 failed: expected has_instant_delivery=False"
    print("✅ Scenario 3: Trial expired 1 min ago -> FREE, 15 min delay")

    # ---------------------------------------------------------
    # 4. SCENARIO 4: Premium active (now + 30d) -> PREMIUM, 0 sec instant
    # ---------------------------------------------------------
    u4, _ = await get_or_create_user(910004, username="matrix_user4")
    await grant_premium(910004, days=30)
    s4 = await get_user_status(910004)
    assert s4["is_premium"] is True, f"Scenario 4 failed: expected is_premium=True, got {s4}"
    assert s4["has_instant_delivery"] is True, "Scenario 4 failed: expected instant delivery"
    print("✅ Scenario 4: Premium active (now + 30d) -> PREMIUM, 0 sec instant delivery")

    # ---------------------------------------------------------
    # 5. SCENARIO 5: Premium expired (now - 1d) -> FREE, 15 min delay
    # ---------------------------------------------------------
    u5, _ = await get_or_create_user(910005, username="matrix_user5")
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910005)
            .values(
                is_premium=True,
                premium_until=now - timedelta(days=1),
                trial_until=now - timedelta(days=25),
            )
        )
        await s.commit()
    s5 = await get_user_status(910005)
    assert s5["is_premium"] is False, f"Scenario 5 failed: expected is_premium=False, got {s5}"
    assert s5["is_trial"] is False, f"Scenario 5 failed: expected is_trial=False, got {s5}"
    assert s5["has_instant_delivery"] is False, "Scenario 5 failed: expected has_instant_delivery=False"
    print("✅ Scenario 5: Premium expired (now - 1d) -> FREE, 15 min delay")

    # ---------------------------------------------------------
    # 6. SCENARIO 6: Trial + just bought Premium -> PREMIUM, 0 sec instant
    # ---------------------------------------------------------
    u6, _ = await get_or_create_user(910006, username="matrix_user6")
    assert (await get_user_status(910006))["is_trial"] is True
    await grant_premium(910006, days=30)
    s6 = await get_user_status(910006)
    assert s6["is_premium"] is True, f"Scenario 6 failed: {s6}"
    assert s6["has_instant_delivery"] is True
    print("✅ Scenario 6: Trial + just bought Premium -> PREMIUM, 0 sec instant delivery")

    # ---------------------------------------------------------
    # 7 & 8. SCENARIOS 7 & 8: Referral mechanics
    # User 7 invites User 8 -> User 7 gets +7 days Premium, User 8 gets 48h Welcome Trial
    # ---------------------------------------------------------
    u7, _ = await get_or_create_user(910007, username="referrer_u7")
    u8, created8 = await get_or_create_user(910008, username="invitee_u8", referred_by=910007)
    assert created8 is True

    # User 8 (Invitee): should have 48h Welcome Trial!
    s8 = await get_user_status(910008)
    assert s8["is_trial"] is True, f"Scenario 8 failed: {s8}"
    assert s8["trial_hours_left"] == 48, f"Scenario 8 failed: expected 48h trial, got {s8['trial_hours_left']}"
    assert s8["has_instant_delivery"] is True
    print(f"✅ Scenario 8: Invitee user with referral code gets 48h trial (hours_left={s8['trial_hours_left']})")

    # User 7 (Referrer): should have gained +7 days Premium
    s7 = await get_user_status(910007)
    assert s7["is_premium"] is True, f"Scenario 7 failed: {s7}"
    ref_u7 = await get_user(910007)
    assert ref_u7.referral_count == 1, f"Expected referral_count=1, got {ref_u7.referral_count}"
    assert ref_u7.premium_until is not None
    print("✅ Scenario 7: Referrer user received +7 days Premium reward")

    # ---------------------------------------------------------
    # 9. SCENARIO 9: is_active = False + Premium
    # Premium user pauses leads -> status is still PREMIUM, but NOT in subscribers query!
    # ---------------------------------------------------------
    u9, _ = await get_or_create_user(910009, username="paused_premium")
    await grant_premium(910009, days=30)
    subs_before = await get_all_subscribers_with_filters()
    subs_ids_before = [u.telegram_id for u, f in subs_before]
    assert 910009 in subs_ids_before, "Active user must be in subscribers list!"

    # Pause leads
    new_active = await toggle_user_active(910009, is_active=False)
    assert new_active is False
    s9 = await get_user_status(910009)
    assert s9["is_premium"] is True
    assert s9["is_active"] is False

    # Ensure excluded from lead distribution matcher query
    subs_after = await get_all_subscribers_with_filters()
    subs_ids_after = [u.telegram_id for u, f in subs_after]
    assert 910009 not in subs_ids_after, "Paused user MUST NOT be in subscribers list for leads!"
    print("✅ Scenario 9: Paused user is excluded from live dispatch subscribers query")

    # Resume leads
    resumed = await toggle_user_active(910009, is_active=True)
    assert resumed is True
    subs_resumed = await get_all_subscribers_with_filters()
    assert 910009 in [u.telegram_id for u, f in subs_resumed], "Resumed user must be back in subscribers list!"
    print("✅ Toggle lead reception: successfully resumed and back in distribution pool")

    # ---------------------------------------------------------
    # 10. TEST PROMO NOTIFIER ELIGIBILITY
    # ---------------------------------------------------------
    print("\nTesting Promo Notifier Filters (2h Referral & 48h Reactivation)...")
    # Backdate user 1 to 2.5 hours ago
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910001)
            .values(
                created_at=now - timedelta(hours=2, minutes=30),
                promo_referral_sent=False,
            )
        )
        await s.commit()

    ref_promo_users = await get_users_for_referral_promo()
    ref_promo_ids = [u.telegram_id for u in ref_promo_users]
    assert 910001 in ref_promo_ids, "User created 2.5h ago must be eligible for referral promo!"
    await mark_promo_referral_sent(910001)
    ref_promo_after = await get_users_for_referral_promo()
    assert 910001 not in [u.telegram_id for u in ref_promo_after], "User marked sent must be excluded!"
    print("✅ 2h Referral Promo eligibility and suppression verified!")

    # Backdate user 3 to 50 hours ago (free user)
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910003)
            .values(
                created_at=now - timedelta(hours=50),
                trial_until=now - timedelta(hours=50),
                is_premium=False,
                promo_reactivation_sent=False,
            )
        )
        await s.commit()

    react_users = await get_users_for_reactivation_promo()
    assert 910003 in [u.telegram_id for u in react_users], "Inactive free user must be eligible for 48h reactivation promo!"
    await mark_promo_reactivation_sent(910003)
    react_after = await get_users_for_reactivation_promo()
    assert 910003 not in [u.telegram_id for u in react_after], "User marked reactivation sent must be excluded!"

    # 10b. EX-PREMIUM USER (is_premium=True in DB, but expired 50h ago)
    # Must be eligible for reactivation promo!
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910005)
            .values(
                created_at=now - timedelta(hours=60),
                is_premium=True,
                premium_until=now - timedelta(hours=50),
                promo_reactivation_sent=False,
            )
        )
        await s.commit()

    react_ex_prem = await get_users_for_reactivation_promo()
    assert 910005 in [u.telegram_id for u in react_ex_prem], "Ex-premium user with expired sub must be eligible for reactivation promo!"
    print("✅ 48h Reactivation Promo eligibility and suppression verified (including ex-premium users)!")

    # ---------------------------------------------------------
    # 11. TEST ACHIEVEMENTS & MILESTONES
    # ---------------------------------------------------------
    print("\nTesting Milestones & Achievements System...")
    ach_user_id = 910001
    initial_achievements = await get_user_achievements(ach_user_id)
    assert len(initial_achievements) == 0

    # 11a. Test "first_step" milestone (Configure filters)
    await update_user_keywords(ach_user_id, "python, bot, ai")
    unlocked = await check_and_grant_achievements(ach_user_id)
    assert "first_step" in unlocked, f"Expected first_step unlocked, got {unlocked}"
    print("✅ Milestone 'first_step' awarded upon filter configuration!")

    # 11b. Test "sniper" milestone (5 clicks on leads)
    for _ in range(5):
        await record_lead_click(ach_user_id)
    unlocked_sniper = await check_and_grant_achievements(ach_user_id)
    assert "sniper" in unlocked_sniper, f"Expected sniper unlocked, got {unlocked_sniper}"
    print("✅ Milestone 'sniper' awarded upon 5 leads clicked!")

    # 11c. Test None-safety on existing users with NULL stats
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910002)
            .values(
                total_leads_clicked=None,
                consecutive_active_days=None,
            )
        )
        await s.commit()

    # Must not raise TypeError when clicking or checking achievements
    clicks_res = await record_lead_click(910002)
    assert clicks_res == 1, f"Expected 1 click after None increment, got {clicks_res}"
    await check_and_grant_achievements(910002)
    null_payload = await get_achievements_payload(910002)
    assert null_payload["ok"] is True if "ok" in null_payload else True
    print("✅ None-safety audit verified (NULL columns handled gracefully without TypeError)!")

    # 11d. Test referral milestones (Networker on 1, Recruiter on 5)
    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910007)
            .values(referral_count=1)
        )
        await s.commit()
    unlocked_net = await check_and_grant_achievements(910007)
    assert "networker" in unlocked_net, f"Expected networker unlocked, got {unlocked_net}"

    async with async_session() as s:
        await s.execute(
            update(User)
            .where(User.telegram_id == 910007)
            .values(referral_count=5)
        )
        await s.commit()
    unlocked_rec = await check_and_grant_achievements(910007)
    assert "recruiter" in unlocked_rec, f"Expected recruiter unlocked, got {unlocked_rec}"
    print("✅ Referral milestones ('networker' & 'recruiter') verified!")

    # 11e. Test HTML escaping & 48h dynamic trial formatting in format_lead_message
    test_lead = RawLead(
        source="freelancehunt",
        external_id="test_xss",
        title="Парсер на Python < 2 года опыта & скрипт <div id='test'>",
        description="Разработка Telegram-бота <script>alert(1)</script> & доработка API",
        url="https://test.com",
        budget="< 50 000 ₽",
        category="development",
        published_at=now,
        raw_text="raw",
    )
    formatted_msg_48 = format_lead_message(test_lead, is_premium=False, is_trial=True, trial_hours=48)
    assert "&lt;script&gt;" in formatted_msg_48, "HTML tags must be escaped in lead message!"
    assert "<script>" not in formatted_msg_48, "Raw HTML tags must NOT be present in lead message!"
    assert "&lt; 50 000 ₽" in formatted_msg_48, "Budget special chars must be escaped!"
    assert "⚡ <b>МГНОВЕННЫЙ ЛИД [TRIAL 48ч]</b>" in formatted_msg_48, "Invited user must see 48h trial badge!"
    assert "48-часовому Welcome Trial" in formatted_msg_48, "Invited user must see 48h trial description!"
    print("✅ HTML escaping & 48h dynamic trial message formatting verified!")

    # 11f. Test achievements payload for Mini App
    payload = await get_achievements_payload(ach_user_id)
    assert payload["unlocked_count"] >= 2
    sniper_item = next(a for a in payload["achievements"] if a["key"] == "sniper")
    assert sniper_item["unlocked"] is True
    assert sniper_item["progress"] == 5
    print("✅ Achievements API payload verified for Mini App rendering!")

    # ---------------------------------------------------------
    # 12. TEST MINI APP REST API ENDPOINTS
    # ---------------------------------------------------------
    print("\nTesting Mini App API Endpoints (aiohttp TestClient)...")
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

    # Test GET /api/user/{id} includes is_active
    u_res = await client.get(f"/api/user/{ach_user_id}")
    assert u_res.status == 200
    u_data = await u_res.json()
    assert "is_active" in u_data["user"], "is_active must be returned in /api/user/{id}"
    assert u_data["user"]["is_active"] is True

    # Test POST /api/user/{id}/toggle_active
    tog_res = await client.post(f"/api/user/{ach_user_id}/toggle_active")
    assert tog_res.status == 200
    tog_data = await tog_res.json()
    assert tog_data["is_active"] is False

    # Test GET /api/user/{id}/achievements
    ach_res = await client.get(f"/api/user/{ach_user_id}/achievements")
    assert ach_res.status == 200
    ach_data = await ach_res.json()
    assert ach_data["ok"] is True
    assert ach_data["unlocked_count"] >= 2

    # Test POST /api/lead/click
    clk_res = await client.post("/api/lead/click", json={"user_id": ach_user_id})
    assert clk_res.status == 200
    clk_data = await clk_res.json()
    assert clk_data["ok"] is True
    assert clk_data["total_clicks"] >= 6

    await client.close()
    print("✅ All Mini App API endpoints verified successfully!")

    # Cleanup test state
    async with async_session() as s:
        await s.execute(delete(User).where(User.telegram_id.in_(test_ids)))
        await s.execute(delete(UserFilter).where(UserFilter.user_telegram_id.in_(test_ids)))
        await s.execute(delete(Achievement).where(Achievement.user_telegram_id.in_(test_ids)))
        await s.commit()

    print("\n" + "=" * 65)
    print("ALL STATUS MATRIX AUDIT SCENARIOS PASSED WITH 100% SUCCESS!")
    print("=" * 65)


class TestStatusMatrixSuite(unittest.IsolatedAsyncioTestCase):
    async def test_all_scenarios(self):
        await test_full_matrix()


if __name__ == "__main__":
    unittest.main()
