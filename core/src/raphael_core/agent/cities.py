"""Finding a city for the weather: Open-Meteo's geocoder, and the names it does not know.

Open-Meteo looks a place up by name, and it is poor at Chinese and Japanese ones: checked live on
2026-10-20, "台北" finds nothing (in any language it is asked in), "台北市" finds Taipei only when
asked in Chinese or Japanese, "高雄" finds a place in China and "東京" finds nothing in Japanese
(only "東京都" does), while "Taipei" and "Kaohsiung" are found in every language. So the cities a
user is likely to have in the settings are mapped to the English name the geocoder knows, and any
other Chinese or Japanese name is tried as it is and then with 市 (city) added, the way it is
written in the geocoder's data.
"""

import re
import unicodedata

# The English names of the cities that are written in Chinese or Japanese: the keys are the
# spellings (Traditional and Simplified Chinese, Japanese kanji and kana) as `city_key` makes them.
_ALIASES = {
    # Taiwan
    "台北, 臺北, 台北市, 臺北市, 台北市區": "Taipei",
    "新北, 新北市": "New Taipei",
    "桃園, 桃園市, 桃园": "Taoyuan",
    "台中, 臺中, 台中市, 臺中市": "Taichung",
    "台南, 臺南, 台南市, 臺南市": "Tainan",
    "高雄, 高雄市": "Kaohsiung",
    "基隆, 基隆市": "Keelung",
    "新竹, 新竹市": "Hsinchu",
    "嘉義, 嘉义, 嘉義市": "Chiayi",
    "花蓮, 花莲, 花蓮市": "Hualien",
    "宜蘭, 宜兰": "Yilan",
    "台東, 臺東, 台东": "Taitung",
    # Japan
    "東京, 東京都, 东京, とうきょう, トウキョウ": "Tokyo",
    "大阪, 大阪市, おおさか, オオサカ": "Osaka",
    "京都, 京都市, きょうと, キョウト": "Kyoto",
    "橫濱, 横浜, 横浜市, 横滨, よこはま, ヨコハマ": "Yokohama",
    "名古屋, 名古屋市, なごや, ナゴヤ": "Nagoya",
    "札幌, 札幌市, さっぽろ, サッポロ": "Sapporo",
    "福岡, 福岡市, ふくおか, フクオカ": "Fukuoka",
    "神戶, 神戸, 神戸市, こうべ, コウベ": "Kobe",
    "仙台, 仙台市, せんだい, センダイ": "Sendai",
    "廣島, 広島, 广岛, ひろしま, ヒロシマ": "Hiroshima",
    "奈良, 奈良市, なら, ナラ": "Nara",
    "那霸, 那覇, 那覇市, なは, ナハ": "Naha",
    # Elsewhere in Asia
    "首爾, 首尔, 서울, ソウル": "Seoul",
    "北京, 北京市, ペキン": "Beijing",
    "上海, 上海市, シャンハイ": "Shanghai",
    "香港, ホンコン": "Hong Kong",
    "澳門, 澳门, マカオ": "Macau",
    "新加坡, シンガポール": "Singapore",
    "曼谷, バンコク": "Bangkok",
    "吉隆坡, クアラルンプール": "Kuala Lumpur",
    "河內, 河内, ハノイ": "Hanoi",
    "胡志明市, ホーチミン": "Ho Chi Minh City",
    "馬尼拉, 马尼拉, マニラ": "Manila",
    "雅加達, 雅加达, ジャカルタ": "Jakarta",
    "杜拜, 迪拜, ドバイ": "Dubai",
    # The rest of the world
    "紐約, 纽约, ニューヨーク": "New York",
    "倫敦, 伦敦, ロンドン": "London",
    "巴黎, パリ": "Paris",
    "洛杉磯, 洛杉矶, ロサンゼルス": "Los Angeles",
    "舊金山, 旧金山, サンフランシスコ": "San Francisco",
    "西雅圖, 西雅图, シアトル": "Seattle",
    "溫哥華, 温哥华, バンクーバー": "Vancouver",
    "多倫多, 多伦多, トロント": "Toronto",
    "雪梨, 悉尼, シドニー": "Sydney",
    "柏林, ベルリン": "Berlin",
    "莫斯科, モスクワ": "Moscow",
    "羅馬, 罗马, ローマ": "Rome",
    "阿姆斯特丹, アムステルダム": "Amsterdam",
}

# The suffixes that already make a Chinese or Japanese name a place of its own: a city, a county,
# a prefecture, a ward, a province.
_PLACE_SUFFIXES = ("市", "縣", "县", "県", "都", "府", "區", "区", "省")


def city_key(name: str) -> str:
    """`name` as the cities are looked up: compatibility-folded, in lower case, with the spaces
    taken out."""
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


ALIASES: dict[str, str] = {
    city_key(spelling): english
    for spellings, english in _ALIASES.items()
    for spelling in spellings.split(", ")
}


def _has_cjk(text: str) -> bool:
    return any(
        "぀" <= char <= "ヿ" or "㐀" <= char <= "鿿" or "가" <= char <= "힯"
        for char in text
    )


# A city's English name counts only as a word of its own: "Rome" is not the end of "Chrome".
_ENGLISH_NAMES = sorted(set(ALIASES.values()), key=len, reverse=True)


def city_in(text: str) -> str | None:
    """The city of the table that `text` names, written as it is there, or None when it names
    none. The longest name wins (新北市 over 新北); of two as long, the one the table lists
    first (台北 in 台北京站)."""
    key = city_key(text)
    spelled = [spelling for spelling in ALIASES if spelling in key]
    if spelled:
        return max(spelled, key=len)
    folded = unicodedata.normalize("NFKC", text).casefold()
    for english in _ENGLISH_NAMES:
        if re.search(rf"(?<![a-z]){re.escape(english.casefold())}(?![a-z])", folded):
            return english
    return None


def geocoding_queries(city: str, language_code: str) -> list[tuple[str, str]]:
    """The `(name, language)` pairs to ask the geocoder for `city`, in order, until one finds it.

    A city of the table is asked for by its English name, in English: the geocoder names the place
    right in English, whatever it does in another language. Another name is asked for as it is, in
    `language_code`; and, when it is Chinese or Japanese and does not end in a place suffix, once
    more with 市 added.
    """
    name = city.strip()
    english = ALIASES.get(city_key(name))
    if english is not None:
        return [(english, "en")]
    queries = [(name, language_code)]
    if _has_cjk(name) and not name.endswith(_PLACE_SUFFIXES):
        queries.append((name + "市", language_code))
    return queries
