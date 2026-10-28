"""The store: the services the user can add with one login, and nothing else.

The catalog is code in the repository, versioned with the app. Nothing is fetched at run time, so
there is no remote path by which a service could be added, and the store works offline. Each entry
is a service whose official MCP server lets a client sign a user in with no app registered
beforehand (the server accepts dynamic client registration, RFC 7591, with PKCE and a public
client), or one that can be signed in to with the device flow (RFC 8628) and a public client id that
is built in: the user types a short code once at the service's own page, and no secret exists
anywhere (GitHub). Anything else that needs a client id or a secret from the user (Slack, Figma,
Google) is not here. An entry whose client id is not filled in yet is left out of the store.

The words are kept in the languages of the window (`zh-TW`, `en`, `ja`), and the window picks.
"""

from dataclasses import dataclass

from .config import checked_url

LANGUAGES = ("zh-TW", "en", "ja")
CATEGORIES = ("docs", "projects", "developer", "payments", "automation", "design", "crm", "analytics")
# The marks the window has (`service-icons.ts`, `service-logos/`): every entry shows its brand's own.
ICONS = ("notion", "linear", "atlassian", "cloudflare", "todoist", "sentry", "stripe", "zapier", "paypal", "monday", "webflow", "wix", "netlify", "neon", "prisma", "airtable", "jam", "fireflies", "close", "calendly", "attio", "granola", "posthog", "postman", "klaviyo", "sanity", "apify", "jotform", "bitly", "context7", "deepwiki", "mslearn", "aws", "huggingface", "exa", "github")
# Entries the store shows first, in this order.
POPULAR = ("notion", "linear", "atlassian", "todoist", "stripe")


@dataclass(frozen=True)
class DeviceLogin:
    """How a service is signed in to with the device flow: the public client id of Raphael's app
    there (no secret exists), what is asked for, and the two endpoints."""

    client_id: str
    scopes: tuple[str, ...]
    device_endpoint: str
    token_endpoint: str


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
    auth: str = "oauth"  # "none": a public server, added with no login; "device": the device flow
    device: DeviceLogin | None = None  # with auth "device"


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
    auth: str = "oauth",
    device: DeviceLogin | None = None,
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
        auth=auth,
        device=device,
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
    # --- more services. Each address is the one the vendor's own documentation gives; its sign-in was
    # checked from the server's public metadata only (OAuth discovery, dynamic client registration for
    # public clients, S256 PKCE). The ones with auth="none" answer with no login at all.
    _service(
        "monday", "monday.com", "monday.com", "https://mcp.monday.com/mcp", "projects", "monday",  # developer.monday.com/api-reference/docs/integrate-with-monday-mcp
        ("查看和更新 monday.com 的看板與項目", "Read and update your monday.com boards and items", "monday.com のボードとアイテムを確認・更新"),
        (
            "讓拉斐爾查詢 monday.com 看板上的項目，也能新增項目、更新狀態和留言。",
            "Lets Raphael look up the items on your monday.com boards, and add items, change status and comment.",
            "monday.com のボード上のアイテムを調べ、アイテムの追加、ステータス更新、コメントができます。",
        ),
        (
            ("monday 上我有哪些待處理的項目", "在 monday 新增一個項目：準備週會", "把那個項目標成完成"),
            ("What items are waiting for me on monday", "Add an item on monday: prepare the weekly meeting", "Mark that item as done"),
            ("monday で自分の未処理のアイテムは？", "monday にアイテムを追加して：週次会議の準備", "そのアイテムを完了にして"),
        ),
    ),
    _service(
        "webflow", "Webflow", "Webflow", "https://mcp.webflow.com/mcp", "design", "webflow",  # developers.webflow.com/mcp/installing/claude-desktop
        ("管理 Webflow 網站與內容", "Manage your Webflow sites and content", "Webflow のサイトとコンテンツを管理"),
        (
            "讓拉斐爾查看你的 Webflow 網站，讀取和編輯 CMS 內容、頁面與 SEO 設定。",
            "Lets Raphael look at your Webflow sites, and read and edit CMS content, pages and SEO settings.",
            "Webflow のサイトを確認し、CMS コンテンツ、ページ、SEO 設定を読み取り・編集できます。",
        ),
        (
            ("列出我的 Webflow 網站", "在部落格新增一篇草稿文章", "哪些頁面還沒填 SEO 標題"),
            ("List my Webflow sites", "Add a draft post to the blog", "Which pages have no SEO title"),
            ("Webflow のサイト一覧を出して", "ブログに下書き記事を追加して", "SEO タイトルが未設定のページは？"),
        ),
    ),
    _service(
        "wix", "Wix", "Wix", "https://mcp.wix.com/mcp", "design", "wix",  # dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp
        ("管理 Wix 網站、商店與預約", "Manage your Wix sites, stores and bookings", "Wix のサイト・ストア・予約を管理"),
        (
            "讓拉斐爾查看你的 Wix 網站，查詢訂單、預約和商品，也能搜尋 Wix 的說明文件。",
            "Lets Raphael look at your Wix sites, check orders, bookings and products, and search the Wix docs.",
            "Wix のサイトを確認し、注文・予約・商品を調べ、Wix のドキュメントを検索できます。",
        ),
        (
            ("我的 Wix 網站有哪些", "今天有哪些新訂單", "下週有哪些預約"),
            ("Which Wix sites do I have", "What new orders came in today", "What bookings are there next week"),
            ("自分の Wix サイトを教えて", "今日の新しい注文は？", "来週の予約は？"),
        ),
    ),
    _service(
        "netlify", "Netlify", "Netlify", "https://netlify-mcp.netlify.app/mcp", "developer", "netlify",  # docs.netlify.com/build/build-with-ai/netlify-mcp-server/
        ("管理 Netlify 的網站與部署", "Manage your Netlify sites and deploys", "Netlify のサイトとデプロイを管理"),
        (
            "讓拉斐爾查看 Netlify 上的網站和部署狀態，也能建立專案與部署。",
            "Lets Raphael check your Netlify sites and deploy status, and create projects and deploys.",
            "Netlify のサイトとデプロイ状況を確認し、プロジェクトの作成やデプロイができます。",
        ),
        (
            ("我的網站最近一次部署成功了嗎", "列出我在 Netlify 的網站", "部署這個專案"),
            ("Did my site's last deploy succeed", "List my Netlify sites", "Deploy this project"),
            ("サイトの直近のデプロイは成功した？", "Netlify のサイト一覧を出して", "このプロジェクトをデプロイして"),
        ),
    ),
    _service(
        "neon", "Neon", "Neon", "https://mcp.neon.tech/mcp", "developer", "neon",  # neon.com/docs/ai/neon-mcp-server
        ("管理 Neon 的 Postgres 資料庫", "Manage your Neon Postgres databases", "Neon の Postgres データベースを管理"),
        (
            "讓拉斐爾查看 Neon 的專案與分支，執行 SQL 查詢，也能查詢資料表結構。",
            "Lets Raphael look at your Neon projects and branches, run SQL queries and read table schemas.",
            "Neon のプロジェクトとブランチを確認し、SQL の実行やテーブル構造の確認ができます。",
        ),
        (
            ("我在 Neon 有哪些專案", "這個資料表有哪些欄位", "查一下最近註冊的使用者數量"),
            ("What projects do I have on Neon", "What columns does this table have", "Count the users who signed up recently"),
            ("Neon にあるプロジェクトを教えて", "このテーブルのカラムは？", "最近登録したユーザー数を調べて"),
        ),
    ),
    _service(
        "prisma", "Prisma", "Prisma", "https://mcp.prisma.io/mcp", "developer", "prisma",  # prisma.io/docs/postgres/integrations/mcp-server
        ("管理 Prisma Postgres 資料庫", "Manage your Prisma Postgres databases", "Prisma Postgres を管理"),
        (
            "讓拉斐爾建立和管理 Prisma Postgres 資料庫，也能搜尋 Prisma 的說明文件。",
            "Lets Raphael create and manage Prisma Postgres databases, and search the Prisma docs.",
            "Prisma Postgres の作成・管理と、Prisma ドキュメントの検索ができます。",
        ),
        (
            ("列出我的 Prisma 資料庫", "建立一個新的資料庫", "Prisma 怎麼設定關聯"),
            ("List my Prisma databases", "Create a new database", "How do I set up a relation in Prisma"),
            ("Prisma のデータベース一覧を出して", "新しいデータベースを作成して", "Prisma でリレーションはどう設定する？"),
        ),
    ),
    _service(
        "airtable", "Airtable", "Airtable", "https://mcp.airtable.com/mcp", "docs", "airtable",  # airtable.com/developers/agents/mcp/getting-started
        ("搜尋和編輯 Airtable 的資料表", "Search and edit your Airtable bases", "Airtable のベースを検索・編集"),
        (
            "讓拉斐爾查詢 Airtable 資料表裡的記錄，也能新增和更新記錄。",
            "Lets Raphael look up records in your Airtable bases, and add and update them.",
            "Airtable のベースにあるレコードを検索し、追加・更新できます。",
        ),
        (
            ("Airtable 的客戶表裡有哪些記錄", "新增一筆記錄到任務表", "把這筆記錄的狀態改成完成"),
            ("What records are in my customers table", "Add a record to the tasks table", "Set this record's status to done"),
            ("Airtable の顧客テーブルのレコードは？", "タスクのテーブルにレコードを追加して", "このレコードのステータスを完了にして"),
        ),
    ),
    _service(
        "jam", "Jam", "Jam", "https://mcp.jam.dev/mcp", "developer", "jam",  # jam.dev/docs/debug-a-jam/mcp
        ("讀取 Jam 的錯誤回報與錄影", "Read Jam bug reports and recordings", "Jam のバグ報告と録画を確認"),
        (
            "讓拉斐爾讀取 Jam 回報裡的主控台記錄、網路請求和留言，協助找出問題。",
            "Lets Raphael read the console logs, network requests and comments in a Jam, to help find the problem.",
            "Jam の報告にあるコンソールログ、ネットワークリクエスト、コメントを読み、原因の調査を手伝います。",
        ),
        (
            ("看一下這個 Jam 回報出了什麼錯", "這個 Jam 裡有哪些失敗的請求", "最近有哪些 Jam 回報"),
            ("What went wrong in this Jam", "Which requests failed in this Jam", "What are my latest Jams"),
            ("この Jam で何が起きた？", "この Jam で失敗したリクエストは？", "最近の Jam は？"),
        ),
    ),
    _service(
        "fireflies", "Fireflies", "Fireflies.ai", "https://api.fireflies.ai/mcp", "docs", "fireflies",  # docs.fireflies.ai/getting-started/mcp-configuration
        ("搜尋 Fireflies 的會議逐字稿", "Search your Fireflies meeting transcripts", "Fireflies の会議の文字起こしを検索"),
        (
            "讓拉斐爾搜尋 Fireflies 錄下的會議，閱讀逐字稿、摘要和待辦事項。",
            "Lets Raphael search the meetings Fireflies recorded, and read transcripts, summaries and action items.",
            "Fireflies が録音した会議を検索し、文字起こし・要約・アクションアイテムを読めます。",
        ),
        (
            ("昨天的會議有哪些決議", "找出提到預算的會議", "上週會議的待辦事項是什麼"),
            ("What was decided in yesterday's meeting", "Find the meetings that mention the budget", "What were the action items from last week's meeting"),
            ("昨日の会議で決まったことは？", "予算に触れた会議を探して", "先週の会議のアクションアイテムは？"),
        ),
    ),
    _service(
        "close", "Close", "Close", "https://mcp.close.com/mcp", "crm", "close",  # developer.close.com/mcp.md
        ("查詢和更新 Close 的客戶資料", "Look up and update your Close CRM", "Close の顧客データを確認・更新"),
        (
            "讓拉斐爾查詢 Close 裡的潛在客戶、聯絡人和商機，也能記錄活動與更新資料。",
            "Lets Raphael look up leads, contacts and opportunities in Close, log activity and update records.",
            "Close のリード・連絡先・商談を調べ、アクティビティの記録やデータの更新ができます。",
        ),
        (
            ("Close 裡這個客戶的最新進度", "列出本週要跟進的潛在客戶", "記錄剛剛的通話"),
            ("What is the latest on this customer in Close", "List the leads to follow up this week", "Log the call I just had"),
            ("Close でこの顧客の最新状況は？", "今週フォローするリードを出して", "さっきの通話を記録して"),
        ),
    ),
    _service(
        "calendly", "Calendly", "Calendly", "https://mcp.calendly.com", "projects", "calendly",  # developer.calendly.com/calendly-mcp-server
        ("查看 Calendly 的行程與可預約時間", "Check your Calendly events and availability", "Calendly の予定と空き時間を確認"),
        (
            "讓拉斐爾查詢 Calendly 的活動類型、已預約的行程和可預約的時段。",
            "Lets Raphael look up your Calendly event types, booked meetings and open time slots.",
            "Calendly のイベントタイプ、予約済みの予定、空き時間を調べられます。",
        ),
        (
            ("我這週在 Calendly 有哪些預約", "我下週哪些時段可以預約", "列出我的活動類型"),
            ("What is booked on my Calendly this week", "When am I free to book next week", "List my event types"),
            ("今週の Calendly の予約は？", "来週の空き時間は？", "イベントタイプを一覧にして"),
        ),
    ),
    _service(
        "attio", "Attio", "Attio", "https://mcp.attio.com/mcp", "crm", "attio",  # attio.com/apps/mcp
        ("查詢和更新 Attio 的客戶關係", "Look up and update your Attio CRM", "Attio の顧客情報を確認・更新"),
        (
            "讓拉斐爾搜尋 Attio 的公司與聯絡人，閱讀筆記，也能新增記錄、筆記和任務。",
            "Lets Raphael search companies and people in Attio, read notes, and add records, notes and tasks.",
            "Attio の企業や連絡先を検索し、ノートを読み、レコード・ノート・タスクを追加できます。",
        ),
        (
            ("Attio 裡這間公司有哪些聯絡人", "在 Attio 加一則筆記", "我有哪些待辦的任務"),
            ("Who are the contacts at this company in Attio", "Add a note in Attio", "What tasks do I have open"),
            ("Attio でこの会社の連絡先は？", "Attio にノートを追加して", "未完了のタスクは？"),
        ),
    ),
    _service(
        "granola", "Granola", "Granola", "https://mcp.granola.ai/mcp", "docs", "granola",  # docs.granola.ai/help-center/sharing/integrations/mcp
        ("搜尋 Granola 的會議筆記", "Search your Granola meeting notes", "Granola の会議ノートを検索"),
        (
            "讓拉斐爾搜尋和閱讀 Granola 的會議筆記與逐字稿。",
            "Lets Raphael search and read your Granola meeting notes and transcripts.",
            "Granola の会議ノートと文字起こしを検索・閲覧できます。",
        ),
        (
            ("上次跟客戶開會討論了什麼", "找出提到合約的會議筆記", "整理這週會議的重點"),
            ("What did we discuss in the last customer meeting", "Find the meeting notes that mention the contract", "Sum up this week's meetings"),
            ("前回の顧客との会議で何を話した？", "契約に触れた会議ノートを探して", "今週の会議の要点をまとめて"),
        ),
    ),
    _service(
        "posthog", "PostHog", "PostHog", "https://mcp.posthog.com/mcp", "analytics", "posthog",  # posthog.com/docs/mcp
        ("查看 PostHog 的數據與功能開關", "Look into PostHog analytics and feature flags", "PostHog の分析とフィーチャーフラグを確認"),
        (
            "讓拉斐爾查詢 PostHog 的產品數據、實驗與錯誤追蹤，也能管理功能開關。",
            "Lets Raphael query PostHog product data, experiments and error tracking, and manage feature flags.",
            "PostHog のプロダクトデータ、実験、エラー追跡を調べ、フィーチャーフラグを管理できます。",
        ),
        (
            ("昨天有多少人登入", "列出目前開啟的功能開關", "最近最常發生的錯誤是什麼"),
            ("How many people signed in yesterday", "List the feature flags that are on", "What is the most frequent error lately"),
            ("昨日のログイン数は？", "オンのフィーチャーフラグを一覧にして", "最近最も多いエラーは？"),
        ),
    ),
    _service(
        "postman", "Postman", "Postman", "https://mcp.postman.com/mcp", "developer", "postman",  # learning.postman.com/docs/reference/postman-api/postman-mcp-server/postman-mcp-remote-server
        ("管理 Postman 的 API 與集合", "Manage your Postman APIs and collections", "Postman の API とコレクションを管理"),
        (
            "讓拉斐爾查看 Postman 工作區裡的集合與 API，也能建立和更新它們。",
            "Lets Raphael look at the collections and APIs in your Postman workspaces, and create and update them.",
            "Postman のワークスペースにあるコレクションと API を確認し、作成・更新できます。",
        ),
        (
            ("我的 Postman 工作區有哪些集合", "在集合裡新增一個請求", "這個 API 有哪些端點"),
            ("What collections are in my Postman workspace", "Add a request to the collection", "What endpoints does this API have"),
            ("Postman のワークスペースにあるコレクションは？", "コレクションにリクエストを追加して", "この API のエンドポイントは？"),
        ),
    ),
    _service(
        "klaviyo", "Klaviyo", "Klaviyo", "https://mcp.klaviyo.com/mcp", "crm", "klaviyo",  # developers.klaviyo.com/en/docs/connect_to_the_klaviyo_mcp_server
        ("查看 Klaviyo 的行銷活動與客戶", "Look into Klaviyo campaigns and profiles", "Klaviyo のキャンペーンと顧客を確認"),
        (
            "讓拉斐爾查詢 Klaviyo 的行銷活動、自動化流程、客戶資料與成效數據。",
            "Lets Raphael look up Klaviyo campaigns, flows, profiles and performance numbers.",
            "Klaviyo のキャンペーン、フロー、顧客プロフィール、成果データを調べられます。",
        ),
        (
            ("上週的電子報開信率是多少", "列出目前啟用的自動化流程", "找出這位客戶的資料"),
            ("What was last week's newsletter open rate", "List the flows that are live", "Find this customer's profile"),
            ("先週のメールの開封率は？", "稼働中のフローを一覧にして", "この顧客のプロフィールを探して"),
        ),
    ),
    _service(
        "sanity", "Sanity", "Sanity", "https://mcp.sanity.io", "docs", "sanity",  # sanity.io/docs/ai/mcp-server
        ("查詢和編輯 Sanity 的內容", "Query and edit your Sanity content", "Sanity のコンテンツを検索・編集"),
        (
            "讓拉斐爾用 GROQ 查詢 Sanity 的文件，也能建立和修改內容。",
            "Lets Raphael query your Sanity documents with GROQ, and create and change content.",
            "GROQ で Sanity のドキュメントを検索し、コンテンツを作成・変更できます。",
        ),
        (
            ("Sanity 裡有哪些尚未發佈的文章", "新增一篇文章草稿", "這個內容型別有哪些欄位"),
            ("Which posts in Sanity are unpublished", "Create a draft post", "What fields does this content type have"),
            ("Sanity で未公開の記事は？", "記事の下書きを作成して", "このコンテンツタイプのフィールドは？"),
        ),
    ),
    _service(
        "apify", "Apify", "Apify", "https://mcp.apify.com", "automation", "apify",  # docs.apify.com/mcp
        ("執行 Apify 的爬蟲與自動化工具", "Run Apify scrapers and automation Actors", "Apify のスクレイパーと自動化ツールを実行"),
        (
            "讓拉斐爾搜尋 Apify 商店裡的工具（Actor），執行它們並讀取結果。",
            "Lets Raphael find Actors in the Apify Store, run them and read their results.",
            "Apify ストアの Actor を探して実行し、結果を読み取れます。",
        ),
        (
            ("用 Apify 抓這個網頁的內容", "找一個能抓取地圖商家的工具", "上次執行的結果是什麼"),
            ("Scrape this page with Apify", "Find an Actor that collects map listings", "What did the last run return"),
            ("Apify でこのページを取得して", "地図の店舗情報を集める Actor を探して", "前回の実行結果は？"),
        ),
    ),
    _service(
        "jotform", "Jotform", "Jotform", "https://mcp.jotform.com", "docs", "jotform",  # jotform.com/developers/mcp
        ("查看 Jotform 的表單與回覆", "Look into your Jotform forms and responses", "Jotform のフォームと回答を確認"),
        (
            "讓拉斐爾查看 Jotform 的表單與收到的回覆，也能建立新表單。",
            "Lets Raphael look at your Jotform forms and the responses they received, and create new forms.",
            "Jotform のフォームと届いた回答を確認し、新しいフォームを作成できます。",
        ),
        (
            ("我的報名表單收到幾筆回覆", "建立一份意見調查表單", "列出最新的回覆"),
            ("How many responses did my sign-up form get", "Create a feedback form", "List the latest responses"),
            ("申込フォームの回答は何件？", "アンケートフォームを作成して", "最新の回答を一覧にして"),
        ),
    ),
    _service(
        "bitly", "Bitly", "Bitly", "https://api-ssl.bitly.com/v4/mcp", "analytics", "bitly",  # dev.bitly.com/bitly-mcp/using-the-bitly-mcp-server/configuration-details/
        ("建立 Bitly 短網址並查看點擊", "Create Bitly links and check their clicks", "Bitly の短縮リンクを作成しクリックを確認"),
        (
            "讓拉斐爾建立短網址和 QR 碼，也能查詢連結的點擊數據。",
            "Lets Raphael create short links and QR codes, and look up how many clicks a link got.",
            "短縮リンクや QR コードを作成し、リンクのクリック数を調べられます。",
        ),
        (
            ("幫我把這個網址縮短", "這個短網址被點了幾次", "為這個連結做一個 QR 碼"),
            ("Shorten this link for me", "How many clicks did this short link get", "Make a QR code for this link"),
            ("このリンクを短縮して", "この短縮リンクは何回クリックされた？", "このリンクの QR コードを作って"),
        ),
    ),
    # --- public servers: no login, no account
    _service(
        "context7", "Context7", "Upstash", "https://mcp.context7.com/mcp", "docs", "context7",  # context7.com/docs/resources/all-clients
        ("查詢程式庫與框架的最新文件", "Look up current docs for libraries and frameworks", "ライブラリとフレームワークの最新ドキュメントを検索"),
        (
            "讓拉斐爾查詢各種程式庫最新版本的說明文件和程式碼範例。不需要登入。",
            "Lets Raphael look up the current docs and code examples of many libraries. No login needed.",
            "さまざまなライブラリの最新ドキュメントとコード例を調べられます。ログインは不要です。",
        ),
        (
            ("React 最新版的 useEffect 怎麼用", "查一下 Next.js 的路由文件", "這個函式庫有範例嗎"),
            ("How does useEffect work in the latest React", "Look up the Next.js routing docs", "Is there an example for this library"),
            ("最新の React の useEffect の使い方は？", "Next.js のルーティングのドキュメントを調べて", "このライブラリのサンプルはある？"),
        ),
        auth="none",
    ),
    _service(
        "deepwiki", "DeepWiki", "Cognition", "https://mcp.deepwiki.com/mcp", "docs", "deepwiki",  # docs.devin.ai/work-with-devin/deepwiki-mcp
        ("讀懂 GitHub 公開專案的結構", "Understand public GitHub repositories", "GitHub の公開リポジトリを理解"),
        (
            "讓拉斐爾閱讀 GitHub 公開專案的說明頁，並回答關於程式碼的問題。不需要登入。",
            "Lets Raphael read the wiki of a public GitHub repository and answer questions about its code. No login needed.",
            "GitHub の公開リポジトリの解説を読み、コードに関する質問に答えます。ログインは不要です。",
        ),
        (
            ("這個專案的架構是什麼", "這個專案怎麼處理驗證", "用 DeepWiki 看 facebook/react"),
            ("What is the architecture of this project", "How does this project handle auth", "Open facebook/react in DeepWiki"),
            ("このプロジェクトの構成は？", "このプロジェクトは認証をどう扱っている？", "DeepWiki で facebook/react を見て"),
        ),
        auth="none",
    ),
    _service(
        "mslearn", "Microsoft Learn", "Microsoft", "https://learn.microsoft.com/api/mcp", "docs", "mslearn",  # learn.microsoft.com/en-us/training/support/mcp
        ("搜尋 Microsoft 官方技術文件", "Search the official Microsoft docs", "Microsoft の公式ドキュメントを検索"),
        (
            "讓拉斐爾搜尋 Microsoft Learn 的技術文件，讀取文章並找出程式碼範例。不需要登入。",
            "Lets Raphael search the Microsoft Learn docs, read articles and find code samples. No login needed.",
            "Microsoft Learn の技術文書を検索し、記事を読み、コード例を探せます。ログインは不要です。",
        ),
        (
            ("Azure Functions 怎麼設定計時觸發", "找 PowerShell 刪除檔案的範例", "Windows 上怎麼設定環境變數"),
            ("How do I set a timer trigger in Azure Functions", "Find a PowerShell sample to delete files", "How do I set an environment variable on Windows"),
            ("Azure Functions のタイマートリガーの設定は？", "ファイルを削除する PowerShell の例を探して", "Windows で環境変数を設定するには？"),
        ),
        auth="none",
    ),
    _service(
        "awsknowledge", "AWS Knowledge", "Amazon Web Services", "https://knowledge-mcp.global.api.aws", "docs", "aws",  # aws.amazon.com/about-aws/whats-new/2025/10/aws-knowledge-mcp-server-generally-available/
        ("搜尋 AWS 官方文件與建議", "Search the official AWS docs and guidance", "AWS の公式ドキュメントとガイドを検索"),
        (
            "讓拉斐爾搜尋和閱讀 AWS 的官方文件、部落格與建議做法。不需要登入。",
            "Lets Raphael search and read the official AWS documentation, blogs and best practices. No login needed.",
            "AWS の公式ドキュメント、ブログ、ベストプラクティスを検索・閲覧できます。ログインは不要です。",
        ),
        (
            ("S3 的儲存類別有哪些", "Lambda 的逾時上限是多少", "這個服務在東京區域能用嗎"),
            ("What storage classes does S3 have", "What is the timeout limit of Lambda", "Is this service available in Tokyo"),
            ("S3 のストレージクラスは？", "Lambda のタイムアウト上限は？", "このサービスは東京リージョンで使える？"),
        ),
        auth="none",
    ),
    _service(
        "cloudflaredocs", "Cloudflare Docs", "Cloudflare", "https://docs.mcp.cloudflare.com/mcp", "docs", "cloudflare",  # developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/
        ("搜尋 Cloudflare 的官方文件", "Search the official Cloudflare docs", "Cloudflare の公式ドキュメントを検索"),
        (
            "讓拉斐爾搜尋 Cloudflare 開發者文件，回答 Workers、DNS 等服務的用法。不需要登入。",
            "Lets Raphael search the Cloudflare developer docs to answer how Workers, DNS and other services work. No login needed.",
            "Cloudflare の開発者ドキュメントを検索し、Workers や DNS などの使い方に答えます。ログインは不要です。",
        ),
        (
            ("Workers 的免費額度是多少", "Cloudflare 怎麼設定 DNS 記錄", "R2 的收費方式"),
            ("What is the free quota of Workers", "How do I set a DNS record on Cloudflare", "How is R2 priced"),
            ("Workers の無料枠は？", "Cloudflare で DNS レコードを設定するには？", "R2 の料金体系は？"),
        ),
        auth="none",
    ),
    _service(
        "huggingface", "Hugging Face", "Hugging Face", "https://huggingface.co/mcp", "developer", "huggingface",  # huggingface.co/docs/hub/agents-mcp
        ("搜尋 Hugging Face 的模型、資料集與論文", "Search Hugging Face models, datasets and papers", "Hugging Face のモデル・データセット・論文を検索"),
        (
            "讓拉斐爾搜尋 Hugging Face Hub 上的模型、資料集、論文和說明文件。不需要登入。",
            "Lets Raphael search models, datasets, papers and docs on the Hugging Face Hub. No login needed.",
            "Hugging Face Hub のモデル、データセット、論文、ドキュメントを検索できます。ログインは不要です。",
        ),
        (
            ("找一個適合摘要的小模型", "最近熱門的語音辨識模型", "這個資料集的授權是什麼"),
            ("Find a small model for summarising", "What speech recognition models are popular lately", "What is this dataset's licence"),
            ("要約に向いた小さなモデルを探して", "最近人気の音声認識モデルは？", "このデータセットのライセンスは？"),
        ),
        auth="none",
    ),
    _service(
        "exa", "Exa", "Exa", "https://mcp.exa.ai/mcp", "docs", "exa",  # exa.ai/docs/reference/exa-mcp
        ("用 Exa 搜尋網路並讀取網頁", "Search the web and read pages with Exa", "Exa でウェブを検索しページを読む"),
        (
            "讓拉斐爾用 Exa 搜尋網路、擷取網頁內容。不需要登入，用量大時可能會被限速。",
            "Lets Raphael search the web and fetch page content with Exa. No login needed; heavy use may be rate limited.",
            "Exa でウェブを検索し、ページの内容を取得できます。ログインは不要ですが、多用すると制限されることがあります。",
        ),
        (
            ("上網查一下這間公司的最新消息", "找幾篇關於這個主題的文章", "讀一下這個網址的內容"),
            ("Search the web for the latest news on this company", "Find a few articles on this topic", "Read the content of this link"),
            ("この会社の最新ニュースをウェブで調べて", "このテーマの記事を何本か探して", "このリンクの内容を読んで"),
        ),
        auth="none",
    ),
)

# The client id of Raphael's OAuth App on GitHub (public; the device flow has no secret). Were it
# empty, GitHub would be left out of the store.
GITHUB_CLIENT_ID = "Ov23liGLo9BDamTP6cM9"


def github_service(client_id: str) -> Service:
    return _service(
        "github", "GitHub", "GitHub", "https://api.githubcopilot.com/mcp/", "developer", "github",
        ("你的程式庫、議題、PR 和通知", "Your repositories, issues, pull requests and notifications", "リポジトリ、Issue、PR、通知"),
        (
            "讓拉斐爾搜尋程式碼、閱讀和整理議題與 PR，也能查看你的通知。",
            "Lets Raphael search code, read and manage issues and pull requests, and check your notifications.",
            "コードの検索、Issue や PR の閲覧・整理、通知の確認ができます。",
        ),
        (
            ("我有哪些待處理的 PR", "在這個程式庫開一個議題", "看一下我的 GitHub 通知"),
            ("Which pull requests are waiting for me", "Open an issue in this repository", "Check my GitHub notifications"),
            ("自分宛ての PR を教えて", "このリポジトリに Issue を作って", "GitHub の通知を確認して"),
        ),
        auth="device",
        device=DeviceLogin(
            client_id=client_id,
            scopes=("repo", "read:org", "read:user", "notifications"),
            device_endpoint="https://github.com/login/device/code",
            token_endpoint="https://github.com/login/oauth/access_token",
        ),
    )


if GITHUB_CLIENT_ID:
    SERVICES = SERVICES + (github_service(GITHUB_CLIENT_ID),)

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
        "auth": service.auth,
        "popular": service.id in POPULAR,
        "summary": service.summary,
        "about": service.about,
        "examples": {lang: list(texts) for lang, texts in service.examples.items()},
    }
