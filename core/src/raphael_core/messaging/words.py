"""What Raphael says in a messaging app, in the three languages of the app."""

# The longest stretch of a transcript that is echoed back to show what was heard.
HEARD_CHARS = 80

_WORDS: dict[str, dict[str, str]] = {
    "zh-TW": {
        "intro": "我是拉斐爾。直接傳訊息給我，我會像在電腦前一樣回答你。傳 /stop 可以中斷正在跑的背景任務。",
        "pair": "這個機器人是私人的。請主人在電腦的拉斐爾裡核准配對碼 {code}（10 分鐘內有效）。",
        "paired": "已連接。現在可以傳訊息給我了。",
        "voice": "目前只能處理文字和語音訊息。",
        "stopped": "已中斷。",
        "nothing": "目前沒有在跑的任務。",
        "confirm": "（這需要你的確認，請到電腦上回答。）",
        "failed": "這次沒有成功，請再試一次。",
        "heard": "聽到：「{text}」",
        "voice_empty": "沒有聽到內容。",
        "voice_large": "這則語音太大了（上限 {mb} MB）。",
    },
    "en": {
        "intro": "I'm Raphael. Send me a message and I'll answer as I would at your computer. Send /stop to interrupt a background task.",
        "pair": "This bot is private. Ask the owner to approve the code {code} in Raphael on the computer (valid for 10 minutes).",
        "paired": "Connected. You can message me now.",
        "voice": "I can only read text and voice messages for now.",
        "stopped": "Stopped.",
        "nothing": "Nothing is running.",
        "confirm": "(This needs your confirmation. Please answer at the computer.)",
        "failed": "That did not work. Please try again.",
        "heard": "Heard: “{text}”",
        "voice_empty": "I didn't hear anything.",
        "voice_large": "That voice message is too large (the limit is {mb} MB).",
    },
    "ja": {
        "intro": "ラファエルです。メッセージを送ってください。パソコンの前と同じように答えます。/stop でバックグラウンドの作業を止められます。",
        "pair": "このボットは非公開です。パソコンのラファエルでコード {code} を承認してください（10 分間有効）。",
        "paired": "接続しました。メッセージを送ってください。",
        "voice": "今はテキストと音声メッセージだけ読めます。",
        "stopped": "止めました。",
        "nothing": "実行中の作業はありません。",
        "confirm": "（確認が必要です。パソコンで答えてください。）",
        "failed": "うまくいきませんでした。もう一度お試しください。",
        "heard": "聞き取り：「{text}」",
        "voice_empty": "何も聞こえませんでした。",
        "voice_large": "この音声メッセージは大きすぎます（上限 {mb} MB）。",
    },
}


def words(language: str, key: str, **values: str) -> str:
    table = _WORDS.get(language, _WORDS["en"])
    return table[key].format(**values)


def heard(language: str, transcript: str) -> str:
    """The line that says what was understood from a voice message."""
    text = " ".join(transcript.split())
    if len(text) > HEARD_CHARS:
        text = text[:HEARD_CHARS].rstrip() + "…"
    return words(language, "heard", text=text)


def compose(prefix: str, text: str, detail: str | None) -> str:
    """The answer as one message: the line of the overlay and, when there is more, the longer text."""
    line = f"{prefix}{text}".strip()
    if detail and detail.strip() and detail.strip() != text.strip():
        return f"{line}\n\n{detail.strip()}" if line else detail.strip()
    return line
