"""The store: the services the user can add with one login, and nothing else.

The catalog is code in the repository, versioned with the app. Nothing is fetched at run time, so
there is no remote path by which a service could be added, and the store works offline. Each entry
is a service whose official MCP server lets a client sign a user in with no app registered
beforehand (the server accepts dynamic client registration, RFC 7591, with PKCE and a public
client); anything that needs a client id from the owner (GitHub, Slack, Figma, Google) is not here.

The words are kept in the languages of the window (`zh-TW`, `en`, `ja`), and the window picks.
"""

from dataclasses import dataclass

from .config import checked_url

LANGUAGES = ("zh-TW", "en", "ja")
CATEGORIES = ("docs", "projects", "developer", "payments", "automation", "design")
# The marks the window has (`service-icons.ts`); an entry with none is drawn as a letter.
ICONS = ("notion", "linear", "atlassian", "cloudflare", "todoist", "sentry", "stripe", "zapier", "paypal")
# Entries the store shows first, in this order.
POPULAR = ("notion", "linear", "atlassian", "todoist", "stripe", "canva")


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    publisher: str
    url: str  # the MCP server, Streamable HTTP
    category: str
    icon: str | None
    summary: dict[str, str]
    about: dict[str, str]
    examples: dict[str, tuple[str, ...]]


def _service(
    ident: str,
    name: str,
    publisher: str,
    url: str,
    category: str,
    icon: str | None,
    summary: tuple[str, str, str],
    about: tuple[str, str, str],
    examples: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> Service:
    return Service(
        id=ident,
        name=name,
        publisher=publisher,
        url=checked_url(url),
        category=category,
        icon=icon,
        summary=dict(zip(LANGUAGES, summary, strict=True)),
        about=dict(zip(LANGUAGES, about, strict=True)),
        examples=dict(zip(LANGUAGES, examples, strict=True)),
    )


SERVICES: tuple[Service, ...] = (
    _service(
        "notion", "Notion", "Notion", "https://mcp.notion.com/mcp", "docs", "notion",
        ("搜尋、閱讀和編輯你的頁面與資料庫", "Search, read and edit your pages and databases", "ページとデータベースを検索・閲覧・編集"),
        (
            "讓拉斐爾在你的 Notion 工作區裡找資料、讀取頁面，也能新增和修改內容。",
            "Lets Raphael find things in your Notion workspace, read pages, and add or change content.",
            "Notion のワークスペースで情報を探し、ページを読み、内容を追加・編集できます。",
        ),
        (
            ("在 Notion 搜尋上週的會議記錄", "把剛剛的想法記到 Notion", "我的 Notion 待辦資料庫裡有什麼"),
            ("Search Notion for last week's meeting notes", "Save that idea to Notion", "What is in my Notion to-do database"),
            ("Notion で先週の議事録を探して", "さっきのアイデアを Notion にメモして", "Notion のタスク一覧を教えて"),
        ),
    ),
    _service(
        "linear", "Linear", "Linear", "https://mcp.linear.app/mcp", "projects", "linear",
        ("查看和建立 Linear 的問題與專案", "Find and create Linear issues and projects", "Linear の課題とプロジェクトを確認・作成"),
        (
            "讓拉斐爾查詢你的 Linear 問題、專案和週期，也能建立問題、留言和更新狀態。",
            "Lets Raphael look up your Linear issues, projects and cycles, and create issues, comment and change status.",
            "Linear の課題・プロジェクト・サイクルを調べ、課題の作成、コメント、ステータス更新ができます。",
        ),
        (
            ("我在 Linear 上有哪些進行中的問題", "在 Linear 建立一個問題：登入頁面壞掉", "把那個問題標成已完成"),
            ("What Linear issues are assigned to me", "Create a Linear issue: the login page is broken", "Mark that issue as done"),
            ("Linear で自分に割り当てられた課題は？", "Linear に課題を作成して：ログイン画面が壊れている", "その課題を完了にして"),
        ),
    ),
    _service(
        "atlassian", "Atlassian", "Atlassian", "https://mcp.atlassian.com/v1/mcp", "projects", "atlassian",
        ("使用 Jira 和 Confluence", "Use Jira and Confluence", "Jira と Confluence を利用"),
        (
            "讓拉斐爾搜尋和更新 Jira 的問題，並閱讀、撰寫 Confluence 的頁面。",
            "Lets Raphael search and update Jira issues, and read and write Confluence pages.",
            "Jira の課題を検索・更新し、Confluence のページを読み書きできます。",
        ),
        (
            ("我在 Jira 上還有哪些未完成的工作", "在 Confluence 找產品規格那一頁", "幫我在 Jira 新增一個工作項目"),
            ("What open Jira issues do I have", "Find the product spec page in Confluence", "Create a Jira issue for me"),
            ("Jira の未完了の課題を教えて", "Confluence で製品仕様のページを探して", "Jira に課題を追加して"),
        ),
    ),
    _service(
        "todoist", "Todoist", "Doist", "https://ai.todoist.net/mcp", "projects", "todoist",
        ("管理 Todoist 的任務與專案", "Manage your Todoist tasks and projects", "Todoist のタスクとプロジェクトを管理"),
        (
            "讓拉斐爾查看今天的任務，新增、完成或重新安排任務。",
            "Lets Raphael show today's tasks, and add, complete or reschedule them.",
            "今日のタスクを確認し、追加・完了・再スケジュールできます。",
        ),
        (
            ("我今天在 Todoist 有哪些任務", "新增任務：明天下午交報告", "把買牛奶標成完成"),
            ("What is on my Todoist today", "Add a task: send the report tomorrow afternoon", "Mark buy milk as done"),
            ("今日の Todoist のタスクは？", "タスクを追加：明日の午後にレポートを提出", "牛乳を買うを完了にして"),
        ),
    ),
    _service(
        "sentry", "Sentry", "Sentry", "https://mcp.sentry.dev/mcp", "developer", "sentry",
        ("查看 Sentry 的錯誤與問題", "Look into Sentry errors and issues", "Sentry のエラーと課題を確認"),
        (
            "讓拉斐爾查詢 Sentry 專案裡的錯誤、堆疊追蹤和發生次數。",
            "Lets Raphael look up the errors, stack traces and counts in your Sentry projects.",
            "Sentry のプロジェクトにあるエラー、スタックトレース、発生回数を調べられます。",
        ),
        (
            ("Sentry 最近有哪些新的錯誤", "這個錯誤發生了幾次", "哪個專案的錯誤最多"),
            ("What new errors came up in Sentry", "How many times did this error happen", "Which project has the most errors"),
            ("Sentry に新しいエラーはある？", "このエラーは何回発生した？", "エラーが一番多いプロジェクトは？"),
        ),
    ),
    _service(
        "cloudflare", "Cloudflare", "Cloudflare", "https://mcp.cloudflare.com/mcp", "developer", "cloudflare",
        ("管理 Cloudflare 的網站與服務", "Manage your Cloudflare sites and services", "Cloudflare のサイトとサービスを管理"),
        (
            "讓拉斐爾查看和設定你 Cloudflare 帳號裡的網域、DNS 記錄與 Workers。",
            "Lets Raphael look at and change the domains, DNS records and Workers in your Cloudflare account.",
            "Cloudflare アカウントのドメイン、DNS レコード、Workers を確認・設定できます。",
        ),
        (
            ("我的網域在 Cloudflare 上有哪些 DNS 記錄", "列出我的 Workers", "最近這個網站的流量怎麼樣"),
            ("What DNS records does my domain have on Cloudflare", "List my Workers", "How is traffic on my site lately"),
            ("Cloudflare でドメインの DNS レコードを教えて", "Workers の一覧を出して", "最近のサイトのトラフィックは？"),
        ),
    ),
    _service(
        "stripe", "Stripe", "Stripe", "https://mcp.stripe.com", "payments", "stripe",
        ("查詢 Stripe 的付款、客戶與帳務", "Look up Stripe payments, customers and billing", "Stripe の支払い・顧客・請求を確認"),
        (
            "讓拉斐爾查詢 Stripe 的付款、客戶和訂閱，也能建立發票或連結。部分動作會在 Stripe 網站上要你再確認。",
            "Lets Raphael look up Stripe payments, customers and subscriptions, and create invoices or links. Some actions ask you to confirm on Stripe's own site.",
            "Stripe の支払い・顧客・サブスクリプションを調べ、請求書やリンクを作成できます。一部の操作は Stripe のサイトで確認が必要です。",
        ),
        (
            ("今天 Stripe 有哪些新的付款", "找出這位客戶的訂閱", "這個月的收入是多少"),
            ("What new payments came in on Stripe today", "Find this customer's subscription", "What is this month's revenue"),
            ("今日の Stripe の新しい支払いは？", "この顧客のサブスクリプションを探して", "今月の売上はいくら？"),
        ),
    ),
    _service(
        "paypal", "PayPal", "PayPal", "https://mcp.paypal.com/mcp", "payments", "paypal",
        ("查詢 PayPal 的交易與發票", "Look up PayPal transactions and invoices", "PayPal の取引と請求書を確認"),
        (
            "讓拉斐爾查詢 PayPal 的交易、發票和訂單，也能建立發票。",
            "Lets Raphael look up PayPal transactions, invoices and orders, and create invoices.",
            "PayPal の取引・請求書・注文を調べ、請求書を作成できます。",
        ),
        (
            ("我最近的 PayPal 交易有哪些", "列出還沒付款的發票", "建立一張發票給這位客戶"),
            ("What are my recent PayPal transactions", "List the unpaid invoices", "Create an invoice for this customer"),
            ("最近の PayPal の取引を教えて", "未払いの請求書を一覧にして", "この顧客に請求書を作成して"),
        ),
    ),
    _service(
        "canva", "Canva", "Canva", "https://mcp.canva.com/mcp", "design", None,
        ("搜尋和建立 Canva 設計", "Search and create Canva designs", "Canva のデザインを検索・作成"),
        (
            "讓拉斐爾在你的 Canva 裡找設計、建立新設計，也能匯出成圖片或 PDF。",
            "Lets Raphael find designs in your Canva, create new ones, and export them as images or PDFs.",
            "Canva でデザインを探し、新しく作成し、画像や PDF として書き出せます。",
        ),
        (
            ("在 Canva 找上次的簡報", "建立一張活動海報", "把這個設計匯出成 PDF"),
            ("Find my last presentation in Canva", "Make a poster for the event", "Export this design as a PDF"),
            ("Canva で前回のプレゼンを探して", "イベントのポスターを作って", "このデザインを PDF で書き出して"),
        ),
    ),
    _service(
        "zapier", "Zapier", "Zapier", "https://mcp.zapier.com/api/mcp/mcp", "automation", "zapier",
        ("執行 Zapier 連接的上千種應用程式動作", "Run actions across thousands of apps through Zapier", "Zapier 経由で多数のアプリを操作"),
        (
            "讓拉斐爾透過你在 Zapier 設定好的動作操作其他應用程式，例如寄信、更新試算表。",
            "Lets Raphael use the actions you set up in Zapier to work with other apps, such as sending mail or updating a spreadsheet.",
            "Zapier に設定したアクションを使って、メール送信やスプレッドシート更新など他のアプリを操作できます。",
        ),
        (
            ("用 Zapier 把這段話寄給我自己", "在試算表新增一列", "列出我可以用的 Zapier 動作"),
            ("Use Zapier to email that to myself", "Add a row to my spreadsheet", "List the Zapier actions I can use"),
            ("Zapier でこれを自分にメールして", "スプレッドシートに行を追加して", "使える Zapier のアクションを一覧にして"),
        ),
    ),
)

_BY_ID = {service.id: service for service in SERVICES}


def find(ident: str) -> Service | None:
    return _BY_ID.get(ident)


def view(service: Service) -> dict:
    """An entry as the window gets it: every language, for the window to pick from."""
    return {
        "id": service.id,
        "name": service.name,
        "publisher": service.publisher,
        "url": service.url,
        "category": service.category,
        "icon": service.icon,
        "popular": service.id in POPULAR,
        "summary": service.summary,
        "about": service.about,
        "examples": {lang: list(texts) for lang, texts in service.examples.items()},
    }
