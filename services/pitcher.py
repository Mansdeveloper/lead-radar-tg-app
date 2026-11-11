import html
import re
from typing import Optional

from services.classifier import detect_lead_category


CATEGORY_EXPERIENCE = {
    "bot": "Недавно реализовал похожий Telegram-бот с базами данных, продуманной логикой и удобной админкой (могу показать рабочие кейсы).",
    "python": "Регулярно реализую подобные задачи на Python — на прошлой неделе запускал аналогичный парсер с обходом защит и структурированной выгрузкой.",
    "frontend": "Специализируюсь на чистой адаптивной верстке и веб-разработке (React / Vue / HTML / Tailwind), делаю быстро, аккуратно и с вниманием к деталям.",
    "backend": "Имею солидный опыт проектирования надежных API и серверной части, настраивал аналогичные интеграции и оптимизировал архитектуру.",
    "mobile": "Разрабатываю кроссплатформенные и нативные мобильные приложения (Flutter / React Native) с плавным UI и стабильной синхронизацией.",
    "design": "Специализируюсь на продуманном UI/UX дизайне в Figma: создаю эстетичные и конвертящие интерфейсы с готовой системой под верстку.",
    "1c": "Есть практический опыт внедрения и доработки решений на 1С и Битрикс, настрою корректный обмен данными без сбоев.",
    "marketing": "Регулярно веду рекламные кампании и оптимизирую воронки в этой нише с упором на окупаемость и снижение стоимости целевого лида.",
    "copywriting": "Пишу структурированные и вовлекающие тексты без воды, которые решают задачу бизнеса и удерживают внимание целевой аудитории.",
    "analytics": "Строю понятные сквозные дашборды и отчеты (Power BI / SQL / Excel), помогая бизнесу принимать решения на основе точных цифр.",
    "devops": "Профессионально настраиваю сервера, Docker-контейнеры и CI/CD пайплайны с гарантией стабильности и безопасности.",
    "gamedev": "Имею опыт разработки игровой механики и оптимизации графики, готов продемонстрировать примеры реализованных проектов.",
    "other": "Имею успешный практический опыт реализации подобных проектов под ключ и готов оперативно приступить к работе.",
}

CATEGORY_QUESTIONS = {
    "bot": "Подскажите, есть ли уже готовая блок-схема сценариев или обсудим структуру бота в диалоге?",
    "python": "Подскажите, в каком формате требуется выгрузка данных (Excel/CSV, Google Таблицы или база данных)?",
    "frontend": "Подскажите, есть ли уже готовый дизайн-макет в Figma или нужно помочь с визуальной частью?",
    "backend": "Подскажите, есть ли готовое ТЗ/спецификация API или согласуем детали интеграции в сообщениях?",
    "mobile": "Подскажите, планируется публикация в App Store / Google Play или на первом этапе нужен тестовый билд?",
    "design": "Подскажите, есть ли референсы по стилю или брендбук, от которых отталкиваемся?",
    "1c": "Подскажите, какая сейчас используется конфигурация 1С и есть ли тестовая база для безопасной отладки?",
    "marketing": "Подскажите, на какой регион планируется кампания и какая текущая стоимость привлечения клиента?",
    "copywriting": "Подскажите, для какой площадки готовится материал и какой целевой объем текста?",
    "analytics": "Подскажите, из каких источников собираются первичные данные и где удобнее видеть итоговый дашборд?",
    "devops": "Подскажите, где развернута текущая инфраструктура (облако/VPS) и есть ли требования к отказоустойчивости?",
    "gamedev": "Подскажите, на какую целевую платформу ориентируемся и есть ли готовые ассеты/диздок?",
    "other": "Подскажите, есть ли детальное ТЗ или обсудим ключевые нюансы и сроки в диалоге?",
}


def _clean_task_essence(title: str, description: str = "") -> str:
    """Extracts a clean, punchy task phrase from title to form sentence 1."""
    raw = (title or "").strip()
    if not raw and description:
        raw = description.strip()

    if not raw:
        return "реализовать данный проект"

    # Remove leading noise prefixes often found in freelance titles
    cleaned = raw
    prefix_patterns = [
        r"^(?:срочно\s+)?(?:требуется|нужен|нужна|нужно|нужны|ищу|ищем|необходимо|требуются)\s+",
        r"^(?:проект:\s*|задача:\s*|заказ:\s*)",
    ]
    for pat in prefix_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

    # Strip trailing punctuation
    cleaned = re.sub(r"[\.\,\!\?\:\;\-]+$", "", cleaned).strip()

    # If title is very short or was emptied, fallback to original title snippet
    if len(cleaned) < 3:
        cleaned = raw[:60].strip()

    # Limit length so sentence 1 is punchy
    if len(cleaned) > 80:
        # Try cutting at comma or dash
        match = re.search(r"^(.*?)(?:,|\s[-–—]\s|\.|\()", cleaned)
        if match and len(match.group(1)) >= 20:
            cleaned = match.group(1).strip()
        else:
            cleaned = cleaned[:80].rsplit(" ", 1)[0].strip()

    return cleaned


def generate_pitch(
    title: str,
    description: str = "",
    budget: str = "",
    category: str = "",
) -> str:
    """
    Generates a personalized, high-converting response for a freelance order
    using the proven 3-sentence formula:
    1. Confirmation of the task in own words.
    2. Proof of relevant stack experience and similar cases.
    3. An engaging, conversational question leading into dialogue.
    """
    t = (title or "").strip()
    d = (description or "").strip()

    # Detect category if not provided or vague
    cat = (category or "").strip().lower()
    if not cat or cat in ("all", "other", "development"):
        detected = detect_lead_category(t, d)
        if detected and detected != "other":
            cat = detected
        elif cat == "development":
            cat = "backend"
        else:
            cat = "other"

    # Normalize category aliases
    if cat in ("bot", "bots", "telegram"):
        cat = "bot"
    elif cat in ("python", "script", "parser"):
        cat = "python"
    elif cat in ("front", "frontend", "web"):
        cat = "frontend"
    elif cat in ("back", "backend", "api"):
        cat = "backend"
    elif cat in ("copy", "copywriting"):
        cat = "copywriting"

    essence = _clean_task_essence(t, d)
    first_word = essence.split()[0] if essence.split() else ""
    # Lowercase first char if not an acronym, brand, or Latin word (e.g. Python, Telegram, 1C)
    if first_word and not any(ch.isupper() for ch in first_word[1:]) and not first_word.isupper():
        if not re.match(r"^[A-Z0-9]", first_word):
            essence_formatted = essence[0].lower() + essence[1:]
        else:
            essence_formatted = essence
    else:
        essence_formatted = essence

    # Sentence 1: Confirmation
    s1 = f"Здравствуйте! Внимательно ознакомился с задачей: {essence_formatted}."

    # Sentence 2: Proof of experience
    s2 = CATEGORY_EXPERIENCE.get(cat, CATEGORY_EXPERIENCE["other"])

    # Sentence 3: Engaging question
    s3 = CATEGORY_QUESTIONS.get(cat, CATEGORY_QUESTIONS["other"])

    return f"{s1} {s2} {s3}"

