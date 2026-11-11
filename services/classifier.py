import html
import re
from typing import Optional

# 1. Category markers mapping for freelance lead classification
CATEGORY_MARKERS: dict[str, list[str]] = {
    "python": [
        "python",
        "питон",
        "парсер",
        "парсинг",
        "django",
        "джанго",
        "fastapi",
        "flask",
        "aiohttp",
        "pandas",
        "numpy",
        "selenium",
        "scrapy",
        "beautifulsoup",
        "bs4",
        "asyncio",
        "data science",
        "нейросеть",
        "нейросети",
        "машинное обучение",
        "ml",
    ],
    "bot": [
        "бот",
        "боты",
        "бота",
        "ботов",
        "боту",
        "ботом",
        "боте",
        "ботам",
        "ботами",
        "ботах",
        "bot",
        "bots",
        "telegram",
        "телеграм",
        "тг",
        "tg bot",
        "тг бот",
        "tma",
        "mini app",
        "мини апп",
        "aiogram",
        "telebot",
        "pyrogram",
        "discord",
        "дискорд",
        "чат-бот",
        "чатбот",
        "чат бот",
        "vk bot",
        "вк бот",
    ],
    "frontend": [
        "frontend",
        "фронтенд",
        "react",
        "vue",
        "angular",
        "next.js",
        "nextjs",
        "nuxt",
        "верстка",
        "вёрстка",
        "верстку",
        "вёрстку",
        "верстки",
        "вёрстки",
        "верстке",
        "вёрстке",
        "версткой",
        "вёрсткой",
        "верстальщик",
        "сверстать",
        "html",
        "css",
        "landing",
        "лендинг",
        "tilda",
        "тильда",
        "тильде",
        "тильду",
        "тильдой",
        "веб-сайт",
        "вебсайт",
        "web",
        "tailwind",
        "svelte",
        "javascript",
        "typescript",
        "лендос",
    ],
    "backend": [
        "backend",
        "бэкенд",
        "бэкэнд",
        "node",
        "nodejs",
        "node.js",
        "express",
        "nest",
        "nestjs",
        "graphql",
        "rest api",
        "api",
        "golang",
        "go",
        "java",
        "spring",
        "c#",
        ".net",
        "dotnet",
        "php",
        "laravel",
        "symfony",
        "yii",
        "sql",
        "postgres",
        "postgresql",
        "mysql",
        "mongodb",
        "redis",
        "микросервис",
        "сервер",
        "серверная",
    ],
    "mobile": [
        "mobile",
        "мобильн",
        "flutter",
        "флаттер",
        "react native",
        "ios",
        "android",
        "андроид",
        "swift",
        "swiftui",
        "kotlin",
        "apk",
        "ipa",
        "xcode",
    ],
    "design": [
        "дизайн",
        "дизайнер",
        "design",
        "designer",
        "figma",
        "фигма",
        "фигме",
        "фигму",
        "фигмой",
        "ui",
        "ux",
        "ui/ux",
        "баннер",
        "баннеры",
        "постер",
        "постеры",
        "логотип",
        "логотипы",
        "лого",
        "макет",
        "макеты",
        "иллюстрация",
        "иллюстрации",
        "иллюстрацию",
        "интерфейс",
        "photoshop",
        "фотошоп",
        "illustrator",
        "веб-дизайн",
        "web design",
        "3d",
        "blender",
        "инфографика",
        "инфографику",
        "инфографики",
        "инфографике",
        "креатив",
    ],
    "copywriting": [
        "копирайт",
        "копирайтинг",
        "рерайт",
        "рерайтинг",
        "статья",
        "статьи",
        "статью",
        "статьей",
        "статей",
        "статьям",
        "текст",
        "тексты",
        "контент",
        "smm",
        "смм",
        "перевод",
        "переводчик",
        "редактор",
        "редактирование",
        "редактура",
        "пост",
        "посты",
        "постинг",
        "сео-текст",
        "seo-текст",
    ],
}

# Category aliases for compatibility (e.g. Mini App tags or user inputs)
CATEGORY_ALIASES: dict[str, str] = {
    "copy": "copywriting",
    "development": "backend",
}

# Currency exchange rates to RUB
USD_TO_RUB = 95.0
EUR_TO_RUB = 102.0
UAH_TO_RUB = 2.3

# Specialized regex patterns for markers with complex inflections or disambiguation needs
MARKER_SPECIAL_PATTERNS: dict[str, str] = {
    # Noun inflections for 'бот' avoiding 'ботинки', 'ботаника'
    "бот": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "боты": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "бота": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "ботов": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "боту": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "ботом": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "боте": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "ботам": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "ботами": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "ботах": r"\bбот(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    # English 'bot' avoiding 'both', 'bottle', 'bother'
    "bot": r"\bbots?\b",
    "bots": r"\bbots?\b",
    # Noun inflections for 'пост' avoiding 'постоянная', 'поставить', 'постепенно', 'постер'
    "пост": r"\bпост(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b|\bпостинг[а-я]*",
    "посты": r"\bпост(?:[аеу]|ом|ы|ов|ам|ами|ах)?\b",
    "постинг": r"\bпостинг[а-я]*",
    # Russian article 'статья' all cases singular & plural avoiding 'стать', 'статистика'
    "статья": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    "статьи": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    "статью": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    "статьей": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    "статей": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    "статьям": r"\bстать(?:[яеию]|ей|ёй|ям|ями|ях)\b|\bстатей\b",
    # Layout 'верстка' all forms
    "верстка": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "вёрстка": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "верстку": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "вёрстку": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "верстки": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "вёрстки": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "верстке": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "вёрстке": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "версткой": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "вёрсткой": r"\bв[её]рстк[а-я]*|\bв[её]рстальщик[а-я]*|\bсверста[а-я]*",
    "верстальщик": r"\bв[её]рстальщик[а-я]*",
    "сверстать": r"\bсверста[а-я]*",
    # Tilda all cases
    "тильда": r"\bтильд[а-я]*",
    "тильде": r"\bтильд[а-я]*",
    "тильду": r"\bтильд[а-я]*",
    "тильдой": r"\bтильд[а-я]*",
    # Figma all cases
    "фигма": r"\bфигм[а-я]*",
    "фигме": r"\bфигм[а-я]*",
    "фигму": r"\bфигм[а-я]*",
    "фигмой": r"\bфигм[а-я]*",
    # Neural nets all cases
    "нейросеть": r"\bнейросет[а-я]*",
    "нейросети": r"\bнейросет[а-я]*",
    # Illustration all cases
    "иллюстрация": r"\bиллюстрац[а-я]*",
    "иллюстрации": r"\bиллюстрац[а-я]*",
    "иллюстрацию": r"\bиллюстрац[а-я]*",
    # Infographics all cases
    "инфографика": r"\bинфографик[а-я]*",
    "инфографику": r"\bинфографик[а-я]*",
    "инфографики": r"\bинфографик[а-я]*",
    "инфографике": r"\bинфографик[а-я]*",
    # Editing / copywriting
    "редактура": r"\bредактир[а-я]*|\bредактор[а-я]*|\bредакур[а-я]*",
    "редактор": r"\bредактир[а-я]*|\bредактор[а-я]*|\bредакур[а-я]*",
    "редактирование": r"\bредактир[а-я]*",
    # Strict boundary markers
    "тг": r"\bтг\b",
    "java": r"\bjava\b(?!script)",
    "express": r"\bexpress(?:\.js)?\b",
    "nest": r"\bnest(?:\.js)?\b",
    "spring": r"\bspring(?:boot)?\b",
    "react": r"\breact(?:\.js)?\b",
    "vue": r"\bvue(?:\.js)?\b",
    "angular": r"\bangular(?:\.js)?\b",
    "постер": r"\bпостер[а-я]*",
    "постеры": r"\bпостер[а-я]*",
}


def _build_category_regex(markers: list[str]) -> re.Pattern:
    """
    Builds a robust, boundary-safe regex for category markers.
    Handles Russian inflections, technical English boundaries, and disambiguation.
    """
    patterns: list[str] = []
    strict_words = {
        "go", "ui", "ux", "ai", "js", "ts", "c#", ".net", "api", "web", "sql", "php",
        "apk", "ipa", "ios", "3d", "smm", "смм", "bs4", "tma", "ml"
    }

    seen_patterns = set()

    for m in markers:
        m_lower = m.lower()
        if m_lower in MARKER_SPECIAL_PATTERNS:
            pat = MARKER_SPECIAL_PATTERNS[m_lower]
            if pat not in seen_patterns:
                patterns.append(pat)
                seen_patterns.add(pat)
            continue

        em = re.escape(m_lower)
        if m_lower in strict_words:
            if m_lower.isalnum():
                pat = r"\b" + em + r"\b"
            else:
                pat = r"(?:\b|(?<=\s))" + em + r"(?:\b|(?=\s|[.,!?;:]|$))"
        elif " " in m_lower or "-" in m_lower or "/" in m_lower or "." in m_lower:
            pat = r"(?:\b|(?<=\s))" + em + r"(?:\b|(?=\s|[.,!?;:]|$))"
        elif re.search(r"[a-zA-Z]", m_lower):
            pat = r"\b" + em + r"(?:s|ed|ing)?\b"
        else:
            pat = r"\b" + em + r"[а-я0-9_-]*"

        if pat not in seen_patterns:
            patterns.append(pat)
            seen_patterns.add(pat)

    return re.compile("|".join(patterns), re.IGNORECASE)


_CATEGORY_REGEX_CACHE: dict[str, re.Pattern] = {
    cat: _build_category_regex(markers) for cat, markers in CATEGORY_MARKERS.items()
}


def detect_lead_category(title: str, description: str, default: str = "other") -> str:
    """
    Detects the best matching category for a lead based on title and description.
    Title matches are weighted higher (3x) than description matches (1x) to ensure
    accurate primary intent recognition. Returns default ('other' or 'all') if no markers match.
    """
    t_clean = html.unescape((title or "").lower())
    d_clean = html.unescape((description or "").lower())

    if not t_clean and not d_clean:
        return default

    scores: dict[str, tuple[int, int]] = {}
    for cat, pattern in _CATEGORY_REGEX_CACHE.items():
        title_matches = len(pattern.findall(t_clean))
        desc_matches = len(pattern.findall(d_clean))
        total_score = title_matches * 3 + desc_matches * 1
        if total_score > 0:
            scores[cat] = (total_score, title_matches)

    if not scores:
        return default

    # Choose category with highest total score; break ties by highest title score
    best_cat = max(scores.items(), key=lambda item: (item[1][0], item[1][1]))[0]
    return best_cat


def parse_budget_amount(budget_str: Optional[str]) -> Optional[int]:
    """
    Extracts numeric budget amount in RUB from text strings.
    Supports currencies USD (95), EUR (102), UAH (2.3) and RUB (1.0).
    Handles multipliers ('50к', '50k', '50 т.р.', '15 тыс'),
    European/Russian dot/comma thousand separators (e.g. '15.000 руб'),
    and unescapes HTML entities like &#8381;.
    Returns None if budget is unspecified, negotiable ('По договоренности'), or unparseable.
    """
    if not budget_str or not isinstance(budget_str, str):
        return None

    cleaned = html.unescape(budget_str.strip().replace("\xa0", " "))
    if not cleaned:
        return None

    lower_s = cleaned.lower()

    # Skip negotiable / undefined budgets if no digits present
    negotiable_markers = ["договор", "по договоренности", "не указан", "уточняется", "предложите"]
    if any(m in lower_s for m in negotiable_markers) and not any(ch.isdigit() for ch in cleaned):
        return None

    # Currency detection
    if any(m in lower_s for m in ["$", "usd", "доллар"]):
        rate = USD_TO_RUB
    elif any(m in lower_s for m in ["€", "eur", "евро"]):
        rate = EUR_TO_RUB
    elif any(m in lower_s for m in ["uah", "грн", "грив", "₴"]):
        rate = UAH_TO_RUB
    else:
        rate = 1.0

    nums: list[float] = []

    # 1. Multiplier checks: '50к', '50k', '15 тыс', '1.5k', '50 т.р.'
    k_pattern = r"(\d+(?:[.,]\d+)?)\s*(?:[kк]|тыс(?:яч[а-я]*)?|т\.р\.?)"
    for m in re.finditer(k_pattern, lower_s):
        try:
            val = float(m.group(1).replace(",", ".")) * 1000.0
            nums.append(val)
        except ValueError:
            pass

    # 2. Extract number candidates (prioritize thousand separators: space, comma, dot)
    candidates = re.findall(
        r"\d{1,3}(?:[,\s.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?",
        cleaned,
    )
    for c in candidates:
        num_clean = c.replace(" ", "")
        # Distinguish thousand separator (e.g. 15.000 or 50,000) from decimal (e.g. 10.50 or 10,5)
        if re.search(r"[.,]\d{3}(?:[.,]\d{3})*(?:[^\d]|$)", num_clean):
            parts = re.split(r"[.,]", num_clean)
            if len(parts[-1]) != 3:
                num_clean = "".join(parts[:-1]) + "." + parts[-1]
            else:
                num_clean = "".join(parts)
        else:
            num_clean = num_clean.replace(",", ".")
        try:
            val = float(num_clean)
            if val > 0:
                nums.append(val)
        except ValueError:
            pass

    if not nums:
        if any(ch.isdigit() for ch in cleaned) and re.search(r"\b0\b", cleaned):
            return 0
        return None

    # In case of range (e.g. 'от 10 000 до 20 000 руб'), use maximum potential budget
    best_num = max(nums)
    return int(round(best_num * rate))
