import json
import logging
from pathlib import Path
from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from config import settings
from database.crud import (
    get_or_create_user,
    get_recent_leads,
    get_user,
    get_user_filters,
    get_user_status,
    record_lead_click,
    toggle_user_active,
    update_user_full_filters,
)
from services.achievements import check_and_grant_achievements, get_achievements_payload

logger = logging.getLogger("WebAppServer")
WEBAPP_DIR = Path(__file__).parent


def create_webapp(bot: Bot) -> web.Application:
    app = web.Application()
    cached_bot_username: str | None = None

    async def get_bot_username() -> str:
        nonlocal cached_bot_username
        if not cached_bot_username:
            try:
                bot_info = await bot.get_me()
                cached_bot_username = bot_info.username or ""
            except Exception as e:
                logger.warning(f"Could not fetch bot username: {e}")
                return "fast_leadss_bot"
        return cached_bot_username

    async def handle_index(request: web.Request) -> web.Response:
        index_file = WEBAPP_DIR / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        return web.Response(text="Lead Radar Mini App: index.html not found", status=404)

    async def get_leads_api(request: web.Request) -> web.Response:
        category = request.query.get("category", "all")
        search = request.query.get("search", "")
        limit = min(int(request.query.get("limit", 50)), 100)

        leads = await get_recent_leads(limit=limit, category=category, search=search)
        data = [
            {
                "id": l.id,
                "source": l.source,
                "title": l.title,
                "description": l.description,
                "url": l.url,
                "budget": l.budget,
                "category": l.category,
                "published_at": l.published_at.isoformat() if l.published_at else "",
            }
            for l in leads
        ]
        return web.json_response({"ok": True, "leads": data})

    async def get_user_api(request: web.Request) -> web.Response:
        user_id_str = request.match_info.get("id")
        try:
            user_id = int(user_id_str)
            if user_id <= 0:
                raise ValueError("User ID must be positive")
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Invalid user ID"}, status=400)

        user = await get_user(user_id)
        if not user:
            user, _ = await get_or_create_user(telegram_id=user_id)

        status = await get_user_status(user_id)
        filters = await get_user_filters(user_id)

        username = await get_bot_username()
        ref_link = f"https://t.me/{username}?start=ref_{user_id}"

        return web.json_response(
            {
                "ok": True,
                "user": {
                    "id": user.telegram_id,
                    "is_premium": status["is_premium"],
                    "is_trial": status["is_trial"],
                    "trial_hours_left": status["trial_hours_left"],
                    "trial_until": user.trial_until.isoformat() if user.trial_until else None,
                    "has_instant_delivery": status["has_instant_delivery"],
                    "is_active": getattr(user, "is_active", True),
                    "premium_until": user.premium_until.strftime("%d.%m.%Y")
                    if user.premium_until
                    else None,
                    "referral_count": user.referral_count,
                    "referral_link": ref_link,
                    "daily_leads_sent": user.daily_leads_sent,
                    "max_daily_leads": settings.FREE_MAX_DAILY_LEADS,
                },
                "filters": {
                    "keywords": filters.keywords,
                    "stop_words": filters.stop_words,
                    "min_budget": filters.min_budget,
                    "categories": filters.categories,
                },
            }
        )

    async def toggle_active_api(request: web.Request) -> web.Response:
        user_id_str = request.match_info.get("id")
        try:
            user_id = int(user_id_str)
            if user_id <= 0:
                raise ValueError("User ID must be positive")
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Invalid user ID"}, status=400)

        new_val = await toggle_user_active(user_id)
        return web.json_response({"ok": True, "is_active": new_val})

    async def get_achievements_api(request: web.Request) -> web.Response:
        user_id_str = request.match_info.get("id")
        try:
            user_id = int(user_id_str)
            if user_id <= 0:
                raise ValueError("User ID must be positive")
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Invalid user ID"}, status=400)

        # Check and grant before returning
        await check_and_grant_achievements(user_id, bot=bot)
        payload = await get_achievements_payload(user_id)
        return web.json_response({"ok": True, **payload})

    async def click_lead_api(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            user_id = int(body.get("user_id", 0))
            if user_id <= 0:
                raise ValueError("Invalid user ID")
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

        clicks = await record_lead_click(user_id)
        newly_unlocked = await check_and_grant_achievements(user_id, bot=bot)
        return web.json_response({"ok": True, "total_clicks": clicks, "unlocked": newly_unlocked})

    async def save_filters_api(request: web.Request) -> web.Response:
        user_id_str = request.match_info.get("id")
        try:
            user_id = int(user_id_str)
            if user_id <= 0:
                raise ValueError("User ID must be positive")
            body = await request.json()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

        keywords = body.get("keywords", "")
        stop_words = body.get("stop_words", "")
        min_budget = int(body.get("min_budget", 0))
        categories = body.get("categories")

        await update_user_full_filters(
            telegram_id=user_id,
            keywords=keywords,
            stop_words=stop_words,
            min_budget=min_budget,
            categories=categories,
        )
        await check_and_grant_achievements(user_id, bot=bot)
        return web.json_response({"ok": True, "message": "Filters saved"})

    async def create_invoice_api(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            user_id = int(body.get("user_id"))
            if user_id <= 0:
                raise ValueError("User ID must be positive")
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

        prices = [
            LabeledPrice(
                label="Lead Radar Premium (30 дней)",
                amount=settings.STARS_PRICE,
            )
        ]

        # Generate direct invoice link for Telegram WebApp
        link = await bot.create_invoice_link(
            title="Lead Radar Premium (30 дней)",
            description="Мгновенные алерты с бирж фриланса без задержки и лимитов.",
            payload=f"sub_30_days_webapp_{user_id}",
            currency="XTR",
            prices=prices,
        )
        return web.json_response({"ok": True, "invoice_link": link})

    # CORS & Routes
    async def add_cors_headers(request: web.Request, response: web.StreamResponse):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"

    app.on_response_prepare.append(add_cors_headers)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/leads", get_leads_api)
    app.router.add_get("/api/user/{id}", get_user_api)
    app.router.add_post("/api/user/{id}/toggle_active", toggle_active_api)
    app.router.add_get("/api/user/{id}/achievements", get_achievements_api)
    app.router.add_post("/api/lead/click", click_lead_api)
    app.router.add_post("/api/user/{id}/filters", save_filters_api)
    app.router.add_post("/api/invoice", create_invoice_api)

    return app
