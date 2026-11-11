import io
import sys
import unittest
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from parsers.base import RawLead
from services.classifier import (
    CATEGORY_MARKERS,
    detect_lead_category,
    parse_budget_amount,
)
from services.matcher import matches_filter, matches_user_filter


class TestLeadClassifier(unittest.TestCase):
    """Unit tests for category detection and budget amount parsing."""

    def test_category_markers_presence(self):
        expected_cats = {"python", "bot", "frontend", "backend", "mobile", "design", "copywriting"}
        self.assertTrue(expected_cats.issubset(set(CATEGORY_MARKERS.keys())))
        for cat in expected_cats:
            self.assertGreater(len(CATEGORY_MARKERS[cat]), 0, f"Category {cat} should have markers")

    def test_detect_each_category(self):
        samples = [
            ("Написать парсер на Python для каталога", "Скрипт сбора данных", "python"),
            ("Разработка Telegram бота для автоворонок", "Бот на aiogram", "bot"),
            ("Сверстать лендинг на React и Tailwind", "Адаптивная верстка страницы", "frontend"),
            ("Создать бэкенд REST API на NestJS и PostgreSQL", "Серверная логика", "backend"),
            ("Разработать мобильное приложение на Flutter", "Приложение под iOS и Android", "mobile"),
            ("Дизайн карточек товара в Figma", "Нужен UI/UX дизайн и макеты", "design"),
            ("Написание серии SEO-статей для блога", "Копирайтинг текстов", "copywriting"),
        ]
        for title, desc, expected_cat in samples:
            with self.subTest(title=title):
                cat = detect_lead_category(title, desc)
                self.assertEqual(cat, expected_cat)

    def test_detect_category_unmatched(self):
        self.assertEqual(detect_lead_category("Уборка офиса в Москве", "Нужен клининг"), "other")
        self.assertEqual(detect_lead_category("", ""), "other")
        self.assertEqual(detect_lead_category("Консультация юриста", "", default="all"), "all")

    def test_detect_category_word_boundary_safety(self):
        # Strict boundary tests preventing false positive triggers:
        # 1. 'постоянная работа' should NOT match 'пост' in copywriting
        self.assertEqual(detect_lead_category("Требуется сотрудник на постоянную работу", "Зарплата высокая"), "other")
        # 2. 'ботинки' should NOT match 'бот' in bot
        self.assertEqual(detect_lead_category("Интернет-магазин обуви: туфли и ботинки", ""), "other")
        # 3. 'both' should NOT match 'bot'
        self.assertNotEqual(detect_lead_category("Need both frontend and backend", ""), "bot")
        # 4. 'javascript' should NOT trigger 'java' in backend
        self.assertEqual(detect_lead_category("Разработка интерфейса на javascript", ""), "frontend")
        # 5. 'Java' with Spring should trigger backend
        self.assertEqual(detect_lead_category("Разработка сервиса на Java Spring", ""), "backend")

    def test_detect_category_russian_inflections(self):
        # Russian case endings (accusative, genitive, prepositional, instrumental):
        self.assertEqual(detect_lead_category("Сделать верстку сайта", ""), "frontend")
        self.assertEqual(detect_lead_category("Ошибки в верстке лендинга", ""), "frontend")
        self.assertEqual(detect_lead_category("Написать статью для блога", ""), "copywriting")
        self.assertEqual(detect_lead_category("Помощь со статьей для VC.ru", ""), "copywriting")
        self.assertEqual(detect_lead_category("Отрисовать карточки в фигме", ""), "design")
        self.assertEqual(detect_lead_category("Сделать сайт на тильде", ""), "frontend")
        self.assertEqual(detect_lead_category("Разработка с помощью нейросетей", ""), "python")
        self.assertEqual(detect_lead_category("Сделать инфографику для маркетплейса", ""), "design")

    def test_detect_category_title_weighting(self):
        # Title is bot (weight 3), description mentions design for the bot (weight 1)
        res = detect_lead_category(
            title="Разработка Telegram бота для сервиса",
            description="Дизайн простой, стандартные кнопки",
        )
        self.assertEqual(res, "bot")

    def test_parse_budget_rubles(self):
        self.assertEqual(parse_budget_amount("50 000 ₽"), 50000)
        self.assertEqual(parse_budget_amount("30 000 руб."), 30000)
        self.assertEqual(parse_budget_amount("15000 руб"), 15000)
        self.assertEqual(parse_budget_amount("50,000 руб"), 50000)
        self.assertEqual(parse_budget_amount("100000"), 100000)
        # Thousand separator with dot
        self.assertEqual(parse_budget_amount("15.000 руб"), 15000)

    def test_parse_budget_multipliers(self):
        self.assertEqual(parse_budget_amount("50к руб"), 50000)
        self.assertEqual(parse_budget_amount("50k"), 50000)
        self.assertEqual(parse_budget_amount("15 тыс"), 15000)
        self.assertEqual(parse_budget_amount("50 т.р."), 50000)
        self.assertEqual(parse_budget_amount("1.5к"), 1500)

    def test_parse_budget_usd(self):
        # Rate: 95
        self.assertEqual(parse_budget_amount("100 $"), 9500)
        self.assertEqual(parse_budget_amount("$150"), 14250)
        self.assertEqual(parse_budget_amount("50 USD"), 4750)
        self.assertEqual(parse_budget_amount("10.5 $"), 998)

    def test_parse_budget_eur(self):
        # Rate: 102
        self.assertEqual(parse_budget_amount("200 €"), 20400)
        self.assertEqual(parse_budget_amount("€50"), 5100)
        self.assertEqual(parse_budget_amount("100 EUR"), 10200)

    def test_parse_budget_uah(self):
        # Rate: 2.3
        self.assertEqual(parse_budget_amount("1 000 грн"), 2300)
        self.assertEqual(parse_budget_amount("500 UAH"), 1150)
        self.assertEqual(parse_budget_amount("12000UAH"), 27600)
        self.assertEqual(parse_budget_amount("1000 ₴"), 2300)

    def test_parse_budget_ranges(self):
        # Should pick maximum potential budget
        self.assertEqual(parse_budget_amount("от 10 000 до 20 000 руб"), 20000)
        self.assertEqual(parse_budget_amount("100 - 200 $"), 19000)

    def test_parse_budget_html_entity(self):
        self.assertEqual(parse_budget_amount("75 000  &#8381;"), 75000)

    def test_parse_budget_negotiable_or_none(self):
        self.assertIsNone(parse_budget_amount("По договоренности"))
        self.assertIsNone(parse_budget_amount("договорная"))
        self.assertIsNone(parse_budget_amount("цена не указана"))
        self.assertIsNone(parse_budget_amount(""))
        self.assertIsNone(parse_budget_amount(None))

    def test_parse_budget_zero(self):
        self.assertEqual(parse_budget_amount("0 руб"), 0)


class TestMatcherFiltering(unittest.TestCase):
    """Unit tests for user filter matching with stop words, budgets, and categories/keywords OR logic."""

    def test_stop_words_precedence(self):
        text = "требуется дипломная работа по программированию на python"
        # Even though python matches keyword/category, stop word 'диплом' rejects it
        matched = matches_filter(
            lead_text=text,
            keywords_str="python",
            stop_words_str="диплом, курсовая",
            categories_str="python",
        )
        self.assertFalse(matched)

    def test_stop_words_word_boundary_safety(self):
        # User has stop word 'бот' (they don't want chatbots).
        # A job posting for 'Удаленная работа на React' contains 'работа' (which includes substring 'бот').
        # With boundary safety, 'работа' must NOT trigger stop word 'бот'!
        matched = matches_filter(
            lead_text="удаленная работа на react и tailwind",
            keywords_str="react",
            stop_words_str="бот, диплом",
            categories_str="frontend",
        )
        self.assertTrue(matched)

    def test_min_budget_filtering(self):
        text = "разработка скрипта на python"
        # Below min_budget
        self.assertFalse(
            matches_filter(
                lead_text=text,
                min_budget=15000,
                lead_budget_str="10 000 ₽",
            )
        )
        # Above or equal min_budget
        self.assertTrue(
            matches_filter(
                lead_text=text,
                min_budget=15000,
                lead_budget_str="20 000 ₽",
            )
        )
        # Multiplier budget ('50к руб' = 50,000 RUB) satisfies min_budget=30,000
        self.assertTrue(
            matches_filter(
                lead_text=text,
                min_budget=30000,
                lead_budget_str="50к руб",
            )
        )
        # Negotiable budget ('По договоренности') must always pass
        self.assertTrue(
            matches_filter(
                lead_text=text,
                min_budget=50000,
                lead_budget_str="По договоренности",
            )
        )

    def test_categories_and_keywords_or_logic(self):
        lead_python = "написание парсера на python для выгрузки цен"
        lead_react = "создать интерфейс личного кабинета на react"
        lead_design = "разработать фирменный стиль и логотип"

        # User wants category 'python' OR keyword 'react'
        user_cat = "python"
        user_kw = "react"

        # lead_python matches category 'python' -> passes
        self.assertTrue(
            matches_filter(
                lead_text=lead_python,
                keywords_str=user_kw,
                categories_str=user_cat,
                lead_category="python",
            )
        )
        # lead_react matches keyword 'react' -> passes
        self.assertTrue(
            matches_filter(
                lead_text=lead_react,
                keywords_str=user_kw,
                categories_str=user_cat,
                lead_category="frontend",
            )
        )
        # lead_design matches neither category nor keyword -> rejected
        self.assertFalse(
            matches_filter(
                lead_text=lead_design,
                keywords_str=user_kw,
                categories_str=user_cat,
                lead_category="design",
            )
        )

    def test_no_category_restriction_matches_all(self):
        text = "простой заказ на фрилансе"
        self.assertTrue(
            matches_filter(lead_text=text, categories_str="all", keywords_str="")
        )
        self.assertTrue(
            matches_filter(lead_text=text, categories_str="", keywords_str="")
        )

    def test_category_only_matching(self):
        lead_bot = "создать чат-бот для телеграм канала"
        # Matches category 'bot' without any keywords specified
        self.assertTrue(
            matches_filter(
                lead_text=lead_bot,
                categories_str="bot,mobile",
                keywords_str="",
                lead_category="bot",
            )
        )
        # Rejected if category not selected
        self.assertFalse(
            matches_filter(
                lead_text=lead_bot,
                categories_str="design,copywriting",
                keywords_str="",
                lead_category="bot",
            )
        )

    def test_matches_user_filter_helper(self):
        from database.models import UserFilter

        uf = UserFilter(
            keywords="fastapi, django",
            stop_words="бартер, бесплатно",
            min_budget=10000,
            categories="python,backend",
        )

        sample_lead_pass = RawLead(
            source="fl_ru",
            external_id="ext_pass_1",
            title="Разработка REST API на FastAPI",
            description="Подключение к PostgreSQL",
            url="https://fl.ru/test1",
            budget="25 000 ₽",
            category="python",
            published_at=None,
            raw_text="разработка rest api на fastapi подключение к postgresql",
        )
        self.assertTrue(matches_user_filter(sample_lead_pass, uf))

        sample_lead_stopword = RawLead(
            source="fl_ru",
            external_id="ext_stop_1",
            title="FastAPI проект бесплатно за отзыв",
            description="Работа по бартеру",
            url="https://fl.ru/test2",
            budget="0 ₽",
            category="python",
            published_at=None,
            raw_text="fastapi проект бесплатно за отзыв",
        )
        self.assertFalse(matches_user_filter(sample_lead_stopword, uf))

        sample_lead_low_budget = RawLead(
            source="fl_ru",
            external_id="ext_low_1",
            title="Поправить баг в Django",
            description="Простая правка",
            url="https://fl.ru/test3",
            budget="2 000 ₽",
            category="python",
            published_at=None,
            raw_text="поправить баг в django",
        )
        self.assertFalse(matches_user_filter(sample_lead_low_budget, uf))


if __name__ == "__main__":
    unittest.main()
