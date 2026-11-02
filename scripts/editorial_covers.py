"""Deterministic newspaper-quality story covers built with Python's stdlib.

Every published article gets a meaningful, readable visual even without a
licensed photo. These are visibly illustrative editorial cards, NOT actual
photographs of fictional events. No accounts, fonts, external assets or APIs.
"""
from __future__ import annotations

import html
import re
import unicodedata

PALETTES = {
    "社会": ("#243c46", "#d65e48"),
    "経済": ("#30433b", "#ad703a"),
    "技術": ("#253c4c", "#38848a"),
    "政治": ("#393b54", "#8e74a6"),
    "暮らし": ("#344b3d", "#5d8966"),
    "スポーツ": ("#333e49", "#cc704e"),
    "エンタメ": ("#493a4b", "#a76183"),
}
CATEGORY_EN = {
    "社会": "SOCIETY", "経済": "ECONOMY", "技術": "TECHNOLOGY",
    "政治": "POLITICS", "暮らし": "LIFESTYLE",
    "スポーツ": "SPORTS", "エンタメ": "ENTERTAINMENT",
}
# The image motif must be traceable to the actual story subject, NOT to a
# random seed or whichever reusable rectangle layout happened to be selected.
SUBJECT_KEYWORDS = (
    ("telephone", ("迷惑電話", "着信", "電話", "通話", "恋活アプリ", "返信")),
    ("weather", ("天気", "低気圧", "気象", "機嫌予報", "降水")),
    ("baseball", ("野球", "球団", "投手", "ホームラン")),
    ("parcel", ("土産", "配達", "配送", "菓子", "物流")),
    ("movie", ("ドラマ", "脚本", "映画", "最終回", "考察")),
    ("security", ("漏洩", "流出", "情報保護", "セキュリティ", "謝罪")),
    ("robot", ("ロボット", "工場", "製造装置")),
    ("laboratory", ("研究所", "研究室", "開所")),
    ("meeting", ("会議", "議長", "役員", "管理職", "会見", "説明会")),
    ("fandom", ("推し", "コラボグッズ", "アイドルグッズ", "ファンイベント")),
)
ART_FALLBACK = {
    "notice": "newspaper", "cards": "document",
    "tower": "laboratory", "robot": "robot",
    "meeting": "meeting", "generic": "newspaper",
}

# The icons are deliberately stylized. An illustrative phone, for example,
# cannot be mistaken for a photo documenting a real phone company or incident.
ICONS = {
    "fandom": '''
      <rect x="40" y="73" width="187" height="135" rx="13"/>
      <path d="M59 100h149M67 184h133"/>
      <path d="M130 167l-44-39c-16-17 3-42 25-23l19 16 19-16c22-19 41 6 25 23z"/>
      <path d="M65 47l7-20m123 20l-7-20M134 40V19"/>
      <circle cx="130" cy="147" r="89" stroke-opacity=".20"/>
    ''',
    "telephone": '''
      <rect x="73" y="22" width="114" height="192" rx="20"/>
      <path d="M107 49h46M105 177h50"/>
      <path d="M109 78c-12 19 31 60 48 44l-13-18-13 7-24-26 7-12z"/>
      <path d="M202 52q24 28 0 56M222 35q39 46 0 92"/>''',
    "weather": '''
      <circle cx="177" cy="68" r="35"/>
      <path d="M178 11v12M178 113v12M119 68h15M216 68h14"/>
      <path d="M49 160c-8-46 40-77 74-53 29-42 88-16 84 20 38 2 42 64 3 64H78c-22 0-31-11-29-31z"/>
      <path d="M95 212l-12 24M137 212l-12 24M179 212l-12 24"/>''',
    "baseball": '''
      <circle cx="130" cy="118" r="93"/>
      <path d="M78 42c47 38 47 115 0 152M182 42c-47 38-47 115 0 152"/>
      <path d="M78 65l-18-11M89 88l-21-8M92 116l-22-3M86 146l-22 4M75 170l-18 11"/>
      <path d="M182 65l18-11M171 88l21-8M168 116l22-3M174 146l22 4M185 170l18 11"/>''',
    "parcel": '''
      <path d="M47 102l83-47 83 47-83 49z"/>
      <path d="M47 102v102l83 47 83-47V102M130 151v100"/>
      <path d="M89 79l81 50M88 24v59M173 24v59"/>
      <path d="M116 55q-49-46-52-10 3 20 52 10zM140 55q49-46 52-10-3 20-52 10z"/>''',
    "movie": '''
      <rect x="48" y="89" width="169" height="128" rx="11"/>
      <path d="M48 124h169M85 90l25 34M136 90l25 34M188 90l25 34"/>
      <path d="M55 76l153-39 13 43-153 40z"/>
      <circle cx="133" cy="171" r="31"/><path d="M123 156l30 15-30 15z"/>''',
    "security": '''
      <rect x="52" y="29" width="156" height="187" rx="12"/>
      <path d="M72 71h116M72 120h116M72 168h116"/>
      <circle cx="88" cy="52" r="5"/><circle cx="88" cy="99" r="5"/>
      <circle cx="88" cy="147" r="5"/><circle cx="88" cy="193" r="5"/>
      <path d="M143 124l42 16-7 45-35 27-35-27-7-45z"/>''',
    "robot": '''
      <path d="M38 220h184M87 196v24M165 196v24"/>
      <rect x="65" y="177" width="128" height="19" rx="5"/>
      <circle cx="89" cy="153" r="24"/>
      <path d="M89 153l47-78"/>
      <circle cx="136" cy="75" r="23"/>
      <path d="M136 75l54 51"/>
      <circle cx="190" cy="126" r="17"/>
      <path d="M190 126l-4 40M175 164l11 21 17-20"/>''',
    "laboratory": '''
      <path d="M81 28l49 49M98 15l-18 18M124 63l24 24"/>
      <path d="M133 83l-32 32 33 32 32-32z"/>
      <path d="M136 148q-21 35 10 40h40M185 188q31 0 24-32"/>
      <path d="M75 220h140M103 204h86M64 220q0-54 59-54"/>
      <circle cx="170" cy="146" r="11"/>''',
    "meeting": '''
      <rect x="34" y="67" width="192" height="126" rx="13"/>
      <path d="M76 105h109M76 127h75"/>
      <path d="M68 193v27M194 193v27"/>
      <circle cx="74" cy="29" r="18"/><circle cx="186" cy="29" r="18"/>
      <path d="M47 62q0-28 27-28M213 62q0-28-27-28"/>
      <path d="M119 170h26"/>''',
    "document": '''
      <path d="M55 37h117l35 35v153H55z"/>
      <path d="M172 37v35h35"/>
      <path d="M81 101h105M81 128h105M81 155h80"/>
      <circle cx="154" cy="186" r="18"/><path d="M146 185l7 8 15-17"/>''',
    "newspaper": '''
      <rect x="40" y="48" width="185" height="172" rx="6"/>
      <path d="M40 83h185M57 63h71M59 105h69v79H59z"/>
      <path d="M140 105h69M140 126h69M140 146h69M140 168h69M59 201h150"/>
      <circle cx="166" cy="58" r="11"/>''',
}


def story_subject(article: dict) -> str:
    # Prioritize strong concrete noun matches; title over generic template art.
    title = str(article.get("title", ""))
    for subject, keywords in SUBJECT_KEYWORDS:
        if any(word in title for word in keywords):
            return subject
    return ART_FALLBACK.get(article.get("art"), "newspaper")


def _char_width(char: str) -> float:
    if char == " ":
        return .43
    if unicodedata.east_asian_width(char) in ("F", "W", "A"):
        return 1
    return .58


def wrap_headline(title: str, capacity: float) -> list[str]:
    """Layout based on display width; safe for Japanese and Latin headlines."""
    clean = re.sub(r"\s+", " ", str(title)).strip() or "5年後の架空ニュース"
    lines: list[str] = []
    current = ""
    width = 0.0
    for char in clean:
        advance = _char_width(char)
        if current and width + advance > capacity:
            # Prefer hanging punctuation to an orphan closing bracket.
            if char in "、。，．！？!?)」』】":
                current += char
                lines.append(current)
                current, width = "", 0
                continue
            lines.append(current.rstrip())
            current, width = "", 0
        if not current and char == " ":
            continue
        current += char
        width += advance
    if current:
        lines.append(current.rstrip())
    return [line for line in lines if line]


def headline_layout(title: str) -> tuple[list[str], int]:
    for size in (56, 53, 50, 47, 44, 41, 38, 35, 32, 29):
        lines = wrap_headline(title, 690 / size)
        if len(lines) <= 4:
            return lines, size
    # Extremely long auto-generated headlines can still be published; the
    # complete, accessible headline remains in page HTML and OGP text.
    lines = wrap_headline(title, 690 / 28)
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1][:-2].rstrip() + "…"
    return lines, 28


def render_cover(article: dict) -> str:
    category = str(article.get("category", "社会"))
    ink, accent = PALETTES.get(category, PALETTES["社会"])
    category_en = CATEGORY_EN.get(category, "FUTURE")
    title = str(article.get("title", ""))
    year = str(article.get("fiction_at", "2031"))[:4]
    if not re.fullmatch(r"\d{4}", year):
        year = "2031"
    lines, font_size = headline_layout(title)
    step = round(font_size * 1.3)
    y0 = 265 + max(0, (3 - len(lines))) * 20
    headline = "".join(
        f'<text x="74" y="{y0 + index * step}" fill="#213039" '
        f'font-size="{font_size}" font-weight="900" '
        f'font-family="Hiragino Kaku Gothic ProN, Yu Gothic, Noto Sans CJK JP, sans-serif">'
        f'{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    motif = ICONS[story_subject(article)]
    # Using a header and headline (instead of a random illustration) makes
    # unknown future stories distinct and legible without any AI image service.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675"
      role="img" aria-label="{html.escape('創作ニュース「' + title + '」の編集部制作表紙', quote=True)}">
      <rect width="1200" height="675" fill="#f8f5ec"/>
      <rect x="0" y="0" width="1200" height="13" fill="{accent}"/>
      <rect x="830" y="13" width="370" height="662" fill="{ink}"/>
      <rect x="71" y="65" width="12" height="44" fill="{accent}"/>
      <text x="105" y="97" font-family="Georgia, serif" font-size="32" font-weight="900" letter-spacing="2" fill="#213039">MIHASSEI NEWS</text>
      <text x="75" y="137" font-family="Hiragino Kaku Gothic ProN, Yu Gothic, sans-serif" font-size="19" fill="#62706d">まだ起きていないニュースを、真面目に。</text>
      <path d="M74 160H756" stroke="#bac0b6" stroke-width="2"/>
      <rect x="74" y="182" width="185" height="38" rx="3" fill="{accent}"/>
      <text x="92" y="209" font-family="Hiragino Kaku Gothic ProN, Yu Gothic, sans-serif" font-size="22" font-weight="800" fill="#fffaf0">{html.escape(category)} / 創作</text>
      <text x="279" y="208" font-family="sans-serif" font-size="18" font-weight="700" fill="#76827d" letter-spacing="2">{category_en}</text>
      {headline}
      <path d="M74 559H754" stroke="{accent}" stroke-width="4"/>
      <text x="74" y="593" font-family="Hiragino Kaku Gothic ProN, Yu Gothic, sans-serif" font-size="19" fill="#626f6a">架空の出来事・編集部制作のイメージ画像</text>
      <text x="74" y="637" font-family="sans-serif" font-size="20" font-weight="900" fill="{ink}" letter-spacing="3">VERIPSA.COM</text>
      <text x="860" y="85" font-family="sans-serif" font-size="18" letter-spacing="4" fill="#dfddd4">FICTIONAL EDITION</text>
      <text x="852" y="173" font-family="Georgia, serif" font-size="82" font-weight="900" fill="#f6f0e4">{year}</text>
      <path d="M864 188H1162" stroke="{accent}" stroke-width="5"/>
      <circle cx="1010" cy="367" r="154" fill="none" stroke="#ffffff" stroke-opacity=".12" stroke-width="2"/>
      <g transform="translate(881 242)" fill="none" stroke="#f2eee1" stroke-width="9"
         stroke-linecap="round" stroke-linejoin="round">{motif}</g>
      <rect x="864" y="567" width="288" height="2" fill="{accent}"/>
      <text x="865" y="608" font-family="sans-serif" font-size="21" font-weight="900" letter-spacing="3" fill="#f2eee1">5 YEARS LATER</text>
      <text x="866" y="641" font-family="sans-serif" font-size="16" letter-spacing="2" fill="#b8c6c2">EDITORIAL ILLUSTRATION</text>
    </svg>'''
