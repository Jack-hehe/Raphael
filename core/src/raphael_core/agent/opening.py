"""Opening anything: what may be opened, and how a spoken name becomes something to open.

"Open Reddit", "open Spotify" and "open Bluetooth settings" are one command, `open`. What it opens is
one of three things: a page of the web (`url`), an app of the Start menu (`app`) or a page of the
Windows settings (`settings`). Which of them a name stands for is found without a list of sites
(`service`): the apps installed, then what opening has taught Raphael (`targets`), then a domain
said outright, then the settings pages below, then one question to the language model.

This module is the part that does not move: the rules that every address passes before it is
opened, whoever suggested it (the speaker, a learned name, the model), and the table of the
settings pages. The rules are strict on purpose. Only `https://` pages of the public web are
opened (`http://` when the speaker said it outright); nothing that names this computer or its
network, no address written as numbers, no login in the address, no other scheme (`file:`,
`javascript:`, `steam:`...), and nothing that ends in a program or a script.
"""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_CHARS = 2048
_MAX_TARGET_CHARS = 60

# Words that wrap a request in politeness. English ones are whole words, taken out wherever they
# are; the Chinese and Japanese ones are taken off the ends.
FILLERS = frozenset(
    "please kindly can could would you the a an my for me now just to it this".split()
)
PREFIXES = tuple(
    sorted(
        ("請你", "請", "麻煩你", "麻煩", "幫我", "幫忙", "你可以", "可以", "能不能", "把", "給我",
         "拜託", "先"),
        key=len,
        reverse=True,
    )
)  # fmt: skip
SUFFIXES = tuple(
    sorted(
        ("好不好", "可以嗎", "好嗎", "一下", "謝謝", "吧", "啦", "啊", "喔", "哦", "嘛", "てください",
         "下さい", "ください", "お願いします", "お願い", "にして", "して", "てほしい", "ほしい",
         "ちょうだい", "くれ", "て", "よ", "ね"),
        key=len,
        reverse=True,
    )
)  # fmt: skip

# What is never opened, as the end of an address or of a name: programs, scripts, installers,
# shortcuts and the like. (A real top-level domain is never one of these.)
EXECUTABLE_ENDINGS = frozenset(
    "exe bat cmd com ps1 vbs vbe js jse msi msix appx lnk url scr hta dll reg pif msc jar wsf cpl"
    .split()
)  # fmt: skip
# ... of which `com` is also the commonest top-level domain, and is kept out of the host check.
_NOT_A_HOST_ENDING = EXECUTABLE_ENDINGS - {"com"}
# Top-level domains (and the names under `arpa`) of the local network, never of the public web.
_LOCAL_ENDINGS = frozenset(
    "local localhost internal lan home corp intranet private localdomain arpa invalid test".split()
)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")


def _host_ok(host: str) -> bool:
    """Whether `host` (ASCII, lower case) is the name of a machine on the public internet: two
    labels or more, a top-level domain of letters (so no address written as numbers), and not one
    that is local, or a program."""
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253:
        return False
    if not all(_LABEL.fullmatch(label) for label in labels) or not _TLD.fullmatch(labels[-1]):
        return False
    return labels[-1] not in _LOCAL_ENDINGS and labels[-1] not in _NOT_A_HOST_ENDING


def validate_url(url: str, *, allow_http: bool = False) -> str | None:
    """`url` (without the white space around it) if it may be opened, else None.

    `allow_http` is for an address the speaker said with `http://` in it.
    """
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS or re.search(r"[\s\x00-\x1f\x7f]", url):
        return None
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in (("https", "http") if allow_http else ("https",)):
        return None
    if not host or parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 80, 443):
        return None
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not _host_ok(ascii_host):
        return None
    last = parts.path.rsplit("/", 1)[-1].rsplit(".", 1)
    if len(last) == 2 and last[1].lower() in EXECUTABLE_ENDINGS:
        return None
    return url


_DOMAIN = re.compile(r"(?:(https?)://)?([^\s/?#:@]+)([/?#]\S*)?", re.IGNORECASE)


def parse_domain(spoken: str) -> str | None:
    """The page that `spoken` names when it is a domain (`reddit.com`, `github.com/notifications`,
    `https://example.org`): an `https://` address, or `http://` when that was said. None when it
    is anything else, or an address that may not be opened."""
    found = _DOMAIN.fullmatch(unicodedata.normalize("NFKC", spoken).strip())
    if found is None or "." not in found.group(2):
        return None
    scheme = (found.group(1) or "https").lower()
    rest = found.group(3) or "/"
    return validate_url(f"{scheme}://{found.group(2).lower()}{rest}", allow_http=scheme == "http")


# --- the target as it is kept ---------------------------------------------------------------------


def clean_target(target: str) -> str:
    """What is to be opened, as it is compared and kept: in lower case with full-width letters made
    plain, the English polite words and the Chinese and Japanese ones at the ends taken off, the
    words one space apart."""
    folded = unicodedata.normalize("NFKC", target).casefold()
    text = " ".join(word for word in folded.split() if word not in FILLERS)
    changed = True
    while changed and text:
        changed = False
        for affix in SUFFIXES:
            if text.endswith(affix) and len(text) > len(affix):
                text, changed = text[: -len(affix)].rstrip(), True
        for affix in PREFIXES:
            if text.startswith(affix) and len(text) > len(affix):
                text, changed = text[len(affix) :].lstrip(), True
    return text[:_MAX_TARGET_CHARS].strip()


# --- the Windows settings pages -------------------------------------------------------------------

# page -> (the ms-settings: address, the names it is asked for by: Traditional Chinese, English and
# Japanese, lower case). A name is on one page only.
SETTINGS_PAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "bluetooth": ("ms-settings:bluetooth", ("bluetooth", "藍牙", "蓝牙", "ブルートゥース")),
    "display": (
        "ms-settings:display",
        ("display", "screen", "monitor", "brightness", "resolution", "顯示", "顯示器", "螢幕",
         "亮度", "解析度", "ディスプレイ", "画面", "明るさ", "解像度"),
    ),
    "sound": (
        "ms-settings:sound",
        ("sound", "audio", "speakers", "音效", "聲音", "音訊", "喇叭", "サウンド", "音声", "スピーカー"),
    ),
    "wifi": (
        "ms-settings:network-wifi",
        ("wifi", "wi-fi", "wlan", "wireless", "無線網路", "無線", "ワイファイ", "無線lan"),
    ),
    "network": (
        "ms-settings:network",
        ("network", "internet", "網路", "網絡", "上網", "ネットワーク", "インターネット"),
    ),
    "ethernet": (
        "ms-settings:network-ethernet",
        ("ethernet", "wired network", "乙太網路", "有線網路", "イーサネット", "有線lan"),
    ),
    "vpn": ("ms-settings:network-vpn", ("vpn", "虛擬私人網路", "ブイピーエヌ")),
    "proxy": ("ms-settings:network-proxy", ("proxy", "代理", "代理伺服器", "プロキシ")),
    "airplane": (
        "ms-settings:network-airplanemode",
        ("airplane mode", "flight mode", "飛航模式", "飛行模式", "機內模式", "飛行機モード",
         "機内モード"),
    ),
    "hotspot": (
        "ms-settings:network-mobilehotspot",
        ("hotspot", "mobile hotspot", "熱點", "行動熱點", "ホットスポット", "モバイルホットスポット"),
    ),
    "update": (
        "ms-settings:windowsupdate",
        ("windows update", "update", "updates", "windows 更新", "更新", "系統更新",
         "windowsアップデート", "アップデート", "更新プログラム"),
    ),
    "about": (
        "ms-settings:about",
        ("about", "system info", "about this pc", "關於", "系統資訊", "バージョン情報", "pcの情報"),
    ),
    "storage": (
        "ms-settings:storagesense",
        ("storage", "disk space", "儲存空間", "儲存", "硬碟空間", "ストレージ"),
    ),
    "power": (
        "ms-settings:powersleep",
        ("power", "sleep", "power and sleep", "電源", "睡眠", "電源與睡眠", "電源とスリープ",
         "スリープ"),
    ),
    "battery": (
        "ms-settings:batterysaver",
        ("battery", "battery saver", "電池", "省電模式", "バッテリー", "バッテリーセーバー"),
    ),
    "notifications": (
        "ms-settings:notifications",
        ("notifications", "notification", "通知", "通知とアクション"),
    ),
    "focus": (
        "ms-settings:quiethours",
        ("focus", "do not disturb", "focus assist", "專注", "勿擾", "勿擾模式", "集中モード",
         "応答不可"),
    ),
    "multitasking": (
        "ms-settings:multitasking",
        ("multitasking", "snap windows", "多工", "多工處理", "マルチタスク"),
    ),
    "clipboard": (
        "ms-settings:clipboard",
        ("clipboard", "clipboard history", "剪貼簿", "剪貼簿記錄", "クリップボード"),
    ),
    "personalization": (
        "ms-settings:personalization",
        ("personalization", "personalize", "個人化", "個人設定", "個人用設定"),
    ),
    "wallpaper": (
        "ms-settings:personalization-background",
        ("wallpaper", "background", "desktop background", "桌布", "背景", "壁紙", "デスクトップの背景"),
    ),
    "colors": (
        "ms-settings:colors",
        ("colors", "colours", "accent color", "dark mode", "light mode", "色彩", "顏色", "深色模式",
         "淺色模式", "ダークモード", "ライトモード", "配色"),
    ),
    "themes": ("ms-settings:themes", ("themes", "theme", "佈景主題", "主題", "テーマ")),
    "lockscreen": (
        "ms-settings:lockscreen",
        ("lock screen", "lockscreen", "鎖定畫面", "鎖定螢幕", "ロック画面"),
    ),
    "taskbar": ("ms-settings:taskbar", ("taskbar", "工作列", "タスクバー")),
    "start": (
        "ms-settings:personalization-start",
        ("start menu", "開始功能表", "開始選單", "スタートメニュー"),
    ),
    "fonts": ("ms-settings:fonts", ("fonts", "font", "字型", "字體", "フォント")),
    "apps": (
        "ms-settings:appsfeatures",
        ("apps", "installed apps", "apps and features", "programs", "uninstall", "應用程式",
         "已安裝的應用程式", "解除安裝", "アプリ", "インストールされているアプリ", "アンインストール"),
    ),
    "defaultapps": (
        "ms-settings:defaultapps",
        ("default apps", "default browser", "預設應用程式", "預設程式", "預設瀏覽器", "既定のアプリ",
         "規定のアプリ"),
    ),
    "startup": (
        "ms-settings:startupapps",
        ("startup apps", "startup", "開機啟動", "啟動應用程式", "開機啟動程式", "スタートアップ"),
    ),
    "accounts": (
        "ms-settings:yourinfo",
        ("accounts", "account", "your info", "帳戶", "帳號", "アカウント"),
    ),
    "signin": (
        "ms-settings:signinoptions",
        ("sign-in options", "sign in options", "password", "pin", "登入選項", "密碼", "サインインオプション",
         "パスワード"),
    ),
    "users": (
        "ms-settings:otherusers",
        ("other users", "users", "family", "家庭", "其他使用者", "使用者", "家族", "ユーザー"),
    ),
    "time": (
        "ms-settings:dateandtime",
        ("date and time", "time", "clock", "time zone", "日期和時間", "日期與時間", "時間", "時區",
         "日付と時刻", "時刻", "タイムゾーン"),
    ),
    "language": (
        "ms-settings:regionlanguage",
        ("language", "region", "language and region", "語言", "語言和區域", "區域", "言語", "地域"),
    ),
    "typing": ("ms-settings:typing", ("typing", "text input", "輸入", "文字輸入", "入力")),
    "keyboard": (
        "ms-settings:easeofaccess-keyboard",
        ("keyboard", "鍵盤", "キーボード"),
    ),
    "mouse": ("ms-settings:mousetouchpad", ("mouse", "滑鼠", "マウス")),
    "touchpad": ("ms-settings:devices-touchpad", ("touchpad", "觸控板", "タッチパッド")),
    "printers": (
        "ms-settings:printers",
        ("printers", "printer", "scanners", "印表機", "掃描器", "プリンター", "スキャナー"),
    ),
    "devices": (
        "ms-settings:connecteddevices",
        ("devices", "connected devices", "裝置", "デバイス"),
    ),
    "usb": ("ms-settings:usb", ("usb", "usb裝置", "usbデバイス")),
    "microphone": (
        "ms-settings:privacy-microphone",
        ("microphone", "mic", "麥克風", "マイク"),
    ),
    "location": (
        "ms-settings:privacy-location",
        ("location", "位置", "定位", "位置情報"),
    ),
    "privacy": ("ms-settings:privacy", ("privacy", "隱私", "隱私權", "プライバシー")),
    "security": (
        "ms-settings:windowsdefender",
        ("windows security", "defender", "antivirus", "virus protection", "windows 安全性",
         "病毒防護", "防毒", "windows セキュリティ", "ウイルス対策"),
    ),
    "recovery": (
        "ms-settings:recovery",
        ("recovery", "reset this pc", "復原", "重設", "重設此電腦", "回復", "リカバリー", "pcをリセット"),
    ),
    "backup": ("ms-settings:backup", ("backup", "備份", "バックアップ")),
    "troubleshoot": (
        "ms-settings:troubleshoot",
        ("troubleshoot", "troubleshooting", "疑難排解", "トラブルシューティング"),
    ),
    "accessibility": (
        "ms-settings:easeofaccess",
        ("accessibility", "ease of access", "協助工具", "輕鬆存取", "アクセシビリティ",
         "簡単操作"),
    ),
    "textsize": (
        "ms-settings:easeofaccess-display",
        ("text size", "文字大小", "文字サイズ"),
    ),
    "magnifier": (
        "ms-settings:easeofaccess-magnifier",
        ("magnifier", "放大鏡", "拡大鏡"),
    ),
    "narrator": (
        "ms-settings:easeofaccess-narrator",
        ("narrator", "screen reader", "朗讀程式", "ナレーター"),
    ),
    "speech": ("ms-settings:speech", ("speech", "語音", "音声認識")),
    "nightlight": (
        "ms-settings:nightlight",
        ("night light", "night mode", "夜間模式", "夜燈", "夜間モード", "ナイトライト"),
    ),
    "gaming": (
        "ms-settings:gaming-gamebar",
        ("game bar", "gaming", "遊戲", "遊戲列", "ゲーム", "ゲームバー"),
    ),
    "project": (
        "ms-settings:project",
        ("second screen", "projecting", "投影", "投影到這台電腦", "プロジェクション"),
    ),
    "datausage": (
        "ms-settings:datausage",
        ("data usage", "資料使用量", "データ使用量"),
    ),
    "activation": ("ms-settings:activation", ("activation", "啟用", "ライセンス認証")),
    "optionalfeatures": (
        "ms-settings:optionalfeatures",
        ("optional features", "選用功能", "オプション機能"),
    ),
    "remotedesktop": (
        "ms-settings:remotedesktop",
        ("remote desktop", "遠端桌面", "リモートデスクトップ"),
    ),
    "developers": (
        "ms-settings:developers",
        ("developer mode", "developers", "開發人員", "開發人員模式", "開発者向け"),
    ),
}
SETTINGS_URIS = frozenset(uri for uri, _ in SETTINGS_PAGES.values())
_PAGE_OF_NAME = {name: uri for uri, names in SETTINGS_PAGES.values() for name in names}

# The words that say "the settings of": taken off a target before it is looked for among the names.
_SETTINGS_WORDS = re.compile(
    r"設定アプリ|設定頁面|設定画面|設定を|の設定|設定|设置|設置|頁面|页面|\bsettings\b|\bsetting\b|\bpage\b"
)


def _contains(text: str, name: str) -> bool:
    """Whether `name` is in `text` as a name: where it starts or ends with a letter or digit of
    ASCII, it is not the middle of a longer word."""
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        before = start == 0 or not (name[0].isascii() and text[start - 1].isascii())
        before = before or not text[start - 1].isalnum()
        after = end == len(text) or not (name[-1].isascii() and text[end].isascii())
        after = after or not text[end].isalnum()
        if before and after:
            return True
        start = text.find(name, start + 1)
    return False


def match_settings(target: str, *, bare: bool) -> str | None:
    """The `ms-settings:` address of the page `target` asks for, or None.

    With the word for settings in it ("bluetooth settings", "藍牙設定"), the target is a page when
    it holds the name of one. Without it, only when it is a name and nothing more, and only if
    `bare`: that is the last stage of finding what to open, after the apps, so that a name that is
    also an app (the camera) is the app. A sentence that only mentions a page is none.
    """
    text = clean_target(target)
    core = " ".join(_SETTINGS_WORDS.sub(" ", text).split())
    if not core:
        return None
    said_settings = core != text
    if core in _PAGE_OF_NAME and (said_settings or bare):
        return _PAGE_OF_NAME[core]
    if said_settings:
        named = [name for name in _PAGE_OF_NAME if len(name) >= 2 and _contains(core, name)]
        if named:
            return _PAGE_OF_NAME[max(named, key=len)]
    return None


@dataclass(frozen=True)
class Opening:
    """What is to be opened, and how it was found."""

    kind: str  # "url", "app" (target: the id of an app of the Start menu) or "settings"
    target: str  # the address, the app id or the ms-settings: address
    step: str  # "settings", "app", "learned", "domain", "model" (what found it; logged)
    # The name to teach Raphael when the opening worked; set for an address the model found.
    learn_as: str | None = None
