// Browser assertions run against the real app after the scene's user interactions.
export function checkRendered(name) {
  if (name === "composer-learn") {
    const calls = window.__look.calls.filter(call => call.method === "dictionary.learn");
    if (calls.length !== 1 || JSON.stringify(calls[0].params.corrections) !== JSON.stringify([{ word: "OpenMausBot", misheard: "Open Mouse Bot" }])) throw new Error("The sent voice correction did not reach dictionary.learn exactly once");
    if (document.querySelectorAll(".toast").length !== 1) throw new Error("A learned correction must show exactly one notice");
    if (!document.querySelector(".toast")?.textContent.includes("OpenMausBot")) throw new Error("The learned name is missing from the notice");
    if (document.querySelector(".toast")?.textContent.includes("???")) throw new Error("The learned notice contains corrupted localized text");
    const toast = document.querySelector(".toast").getBoundingClientRect();
    for (const form of document.querySelectorAll(".composer")) {
      const box = form.getBoundingClientRect();
      if (box.width > 0 && box.height > 0 && toast.left < box.right && toast.right > box.left && toast.top < box.bottom && toast.bottom > box.top) throw new Error("The learned notice covers the composer");
    }
    return { check: name, corrections: calls[0].params.corrections };
  }
  const visible = element => element && element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
  const require = (condition, message) => { if (!condition) throw new Error(`${name}: ${message}`); };
  if (name === "room-discussion") {
    require(document.querySelectorAll('.au-room-reply').length >= 3, "multiple replies identify their colleagues");
    require(document.querySelector('.au-room-thread').textContent.includes('我不同意'), "members can disagree");
    require(visible(document.querySelector('.au-room-crown')), "Raphael has the chief badge");
    const crown = document.querySelector('.au-room-crown').getBoundingClientRect();
    const face = document.querySelector('.au-room-crown').parentElement.getBoundingClientRect();
    require(crown.right <= face.right && crown.left >= face.left, "the chief badge stays inside its participant slot");
    require(document.querySelectorAll('.au-room-role').length >= 3, "member roles remain visible");
    return { check: name, replyChips: document.querySelectorAll('.au-room-reply').length };
  }
  if (name === "room-wrapup") {
    const card = document.querySelector('.au-room-wrapup');
    require(visible(card), "wrap-up is a distinct card");
    require(getComputedStyle(card.querySelector('.ans')).borderLeftWidth === '3px', "the wrap-up retains its card accent");
    require([...card.querySelectorAll('.au-room-wrapup-section h3')].map(heading => heading.textContent).join('|') === '結論|待你決定|下一步', "wrap-up has three labelled sections");
    require(card.querySelectorAll('.au-room-wrapup-owner').length === 3, "next steps style each named owner");
    for (const text of ['小晴：', '小森：', '拉斐爾：']) require(card.textContent.includes(text), `wrap-up preserves ${text}`);
    require(card.getBoundingClientRect().right <= innerWidth, "the wrap-up fits the viewport");
    return { check: name, author: 0 };
  }
  if (name === "room-responder") {
    const menu = document.querySelector('#app-popover');
    for (const value of ['auto', 'member:0', 'member:1', 'everyone']) require(visible(menu?.querySelector(`[data-value="${value}"]`)), `${value} is selectable`);
    require(!document.querySelector('.mb-dialog select'), "responder uses the app's selector");
    return { check: name, choices: 4 };
  }
  if (name === "room-reply-cancel") {
    require(!visible(document.querySelector('.cmp-quote')), "cancel removes the reply recipient");
    require(!window.__look.calls.some(call => call.method === 'rooms.send'), "cancel does not send a message");
    return { check: name, cancelled: true };
  }
  if (name === "room-reply-send") {
    require(window.__look.calls.some(call => call.method === 'rooms.send' && call.params.reply_to === 2), "reply reaches the core with the target message id");
    return { check: name, replyTo: 2 };
  }
  if (name === "room-stop") {
    require(window.__look.calls.some(call => call.method === 'rooms.stop' && call.params.room_id === 1), "stop reaches the core");
    require(document.querySelector('[data-room-act="stop"]')?.disabled, "stop stays disabled until the speaker finishes");
    return { check: name, stopRequested: true };
  }
  if (name === "room-replies") {
    const chip = document.querySelector('.au-room-reply');
    require(visible(chip) && chip.textContent === "回覆 小晴", "the reply identifies its recipient");
    require(chip.getBoundingClientRect().right <= innerWidth, "the reply chip fits the viewport");
    return { check: name, recipient: 1 };
  }
  if (name === "room-thinking") {
    const rows = [...document.querySelectorAll('.au-room-thinking')];
    require(rows.length === 1 && visible(rows[0]), "only one member is shown speaking");
    require(getComputedStyle(rows[0]).flexDirection === 'row', "speaker face and status share one row");
    require(visible(rows[0].querySelector('.av')), "the speaker's face is visible");
    require(rows[0].textContent === "小晴 正在發言…", "the speaking row names the member");
    return { check: name, speakingRows: rows.length };
  }
  if (name === "room-row-menu") {
    require(!document.querySelector('[data-room-delete]'), "room rows have no direct delete button");
    const menu = document.querySelector('#app-popover');
    for (const action of ["rename", "members", "delete"]) require(visible(menu?.querySelector(`[data-pick="${action}"]`)), `${action} is available from the room menu`);
    return { check: name, actions: 3 };
  }
  if (name === "team-mentions" || name === "slash-commands") {
    const input = [...document.querySelectorAll('.composer textarea')].find(visible);
    const menu = document.querySelector('#app-popover');
    require(visible(input) && visible(menu), "the completion menu and its field are visible");
    const at = name === "team-mentions" ? input.value.lastIndexOf('@', input.selectionStart) : 0;
    require(at >= 0, "a completion character is present");
    const before = input.value.slice(0, at), prefix = before.split('\n').at(-1);
    const style = getComputedStyle(input), field = input.getBoundingClientRect(), box = menu.getBoundingClientRect();
    const context = document.createElement('canvas').getContext('2d');
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const spacing = parseFloat(style.letterSpacing) || 0;
    const expected = field.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft) + context.measureText(prefix).width + prefix.length * spacing - input.scrollLeft;
    require(Math.abs(box.left - expected) <= 24, `menu starts beside the completion character: ${box.left} versus ${expected}`);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
    const lineTop = field.top + parseFloat(style.borderTopWidth) + parseFloat(style.paddingTop) + (before.split('\n').length - 1) * lineHeight - input.scrollTop;
    require(box.bottom <= lineTop + 4 || box.top >= lineTop + lineHeight - 4, "the menu sits above or below its text line");
    require(box.left >= 8 && box.right <= innerWidth - 8, "the menu stays inside the viewport");
    for (const item of menu.querySelectorAll('button')) {
      require(!item.querySelector('.ck'), "text completions do not reserve an empty icon/check column");
      const label = item.querySelector('.mt'), itemBox = item.getBoundingClientRect();
      require(label && label.getBoundingClientRect().left - itemBox.left <= 12, "completion text starts at the row padding");
    }
    return { check: name, menuLeft: box.left, characterX: expected, delta: Math.abs(box.left - expected) };
  }
  if (name === "home-overview") {
    const home = document.querySelector('.page-home:not([hidden])');
    const emblem = home?.querySelector('.ho-hero > .av');
    require(visible(emblem) && emblem.getBoundingClientRect().width >= 60, "Raphael's emblem is large on Home");
    require(!home.querySelector('h1'), "Home has no headline or slogan");
    const ask = home.querySelector('.ho-ask');
    require(visible(ask) && !ask.textContent.trim() && ask.getAttribute('aria-label'), "asking Raphael is a small labelled icon, not words");
    require(!home.querySelector('.ho-pending [data-home-item^="routine:"], .ho-pending [data-home-item^="report:"]'), "Pending lists only what waits for the owner");
    for (const card of home.querySelectorAll('.ho-card')) require(visible(card.querySelector(':scope > header .ho-open')), "every card opens its details from an icon");
    require(document.querySelector('#nav .nav[aria-current="page"]')?.dataset.go === "home", "Home is the selected rail destination");
    require(document.querySelector('#nav .nav')?.dataset.go === "home", "Home is first in the rail");
    for (const chart of home.querySelectorAll('.ho-bars')) require(chart.getAttribute('aria-label'), "charts have an accessible description");
    return { check: name, items: home.querySelectorAll('[data-home-item]').length, width: home.clientWidth };
  }
  if (name === "home-report") {
    require(window.__look.calls.some(call => call.method === "agent.history.list" && call.params.member_id === 1), "Reported member history was requested");
    require(visible(document.querySelector('.tn[data-id="2"]')), "The reported turn is rendered");
    require(window.__look.calls.some(call => call.method === "agent.history.list" && call.params.conversation_id === 11 && call.params.around_id === 2), "The reported conversation was loaded around its exact turn");
    return { check: name, turnId: 2, memberId: 1 };
  }
  if (name === "calendar-shell") {
    const grid = document.querySelector('.au-page:not([hidden]) .au-calendar-scroll'), side = document.querySelector('.au-page:not([hidden]) .au-calendar-side');
    require(visible(grid), "the calendar grid is visible");
    require(grid.scrollWidth <= grid.clientWidth + 1, "the calendar never scrolls horizontally");
    const box = grid.getBoundingClientRect(), columns = [...grid.querySelectorAll('.au-day-col')];
    for (const column of columns) {
      const day = column.getBoundingClientRect();
      require(day.left >= box.left && day.right <= box.left + grid.clientWidth + 1, "every day fits inside the calendar viewport");
    }
    require(!document.querySelector('.au-page [data-view]'), "the calendar has no list view");
    require(document.querySelector('#nav .nav[aria-current="page"]')?.dataset.go === "home", "the calendar belongs to Home in the rail");
    require(!document.querySelector('#nav .nav[data-go="automations"]'), "the rail has no automations entry");
    const compact = grid.closest('.au-page').clientWidth <= 960;
    require(visible(side) !== compact, "the side panel is hidden below 960px of content width");
    require(visible(document.querySelector('[data-au="tools"]')) === compact, "compact calendar tools have a toolbar icon");
    if (visible(side)) {
      require(side.getBoundingClientRect().left >= grid.getBoundingClientRect().right - 1, "the month and members sit on the right");
      for (const day of side.querySelectorAll('.au-month-grid > span')) {
        require(getComputedStyle(day).whiteSpace === "nowrap" && day.scrollWidth <= day.clientWidth + 1, "month weekday labels fit on one line");
      }
    }
    return { check: name, side: visible(side), days: columns.length, clientWidth: grid.clientWidth, scrollWidth: grid.scrollWidth };
  }
  if (name === "calendar-tools" || name === "calendar-tools-awake") {
    const panel = [...document.querySelectorAll('.au-calendar-side')].find(visible);
    require(panel && visible(panel.querySelector('[data-au="awake"]')) && visible(panel.querySelector('[data-drag-member]')), "month, members and keep-awake remain available");
    const weekdays = [...panel.querySelectorAll('.au-month-grid > span')];
    require(weekdays.map(day => day.textContent).join(' ') === '一 二 三 四 五 六 日', "mini month uses single-character weekdays");
    for (const day of weekdays) require(day.scrollWidth <= day.clientWidth + 1, "mini month weekdays fit");
    const popover = document.querySelector('.au-calendar-tools');
    if (popover) {
      const box = popover.getBoundingClientRect();
      require(box.left >= 8 && box.right <= innerWidth - 8 && box.bottom <= innerHeight - 8, "calendar tools fit inside the viewport");
    }
    if (name === "calendar-tools-awake") {
      require(panel.querySelector('[data-au="awake"]').getAttribute('aria-checked') === 'true', "keep-awake persists after redrawing the tools");
      require(window.__look.calls.some(call => call.command === 'schedule_keep_awake_set' && call.enabled), "keep-awake reaches the native boundary");
    }
    return { check: name, popover: !!popover, weekdays: weekdays.map(day => day.textContent) };
  }
  if (name === "team-board-space") {
    const workspace = document.querySelector('.team-board-scroll').getBoundingClientRect();
    const groups = [...document.querySelectorAll('.team-group:not([hidden])')].map(group => group.getBoundingClientRect());
    if (innerWidth < 1400 || !groups.length) return { check: name, wide: false };
    const left = Math.min(...groups.map(box => box.left)), right = Math.max(...groups.map(box => box.right));
    const widthShare = (right - left) / workspace.width;
    const areaShare = groups.reduce((sum, box) => sum + box.width * box.height, 0) / (workspace.width * workspace.height);
    require(widthShare >= 0.8, "R-UI-01: team groups must use at least 80% of the workspace width");
    require(areaShare >= 0.35, "R-UI-01: team groups must use at least 35% of the workspace area");
    require(groups.every(box => box.left >= workspace.left && box.right <= workspace.right && box.bottom <= workspace.bottom), "R-UI-01: the default board fits its workspace");
    return { check: name, wide: true, widthShare, areaShare };
  }
  if (name === "team-header") {
    const page = document.querySelector('.team-page'), header = page.querySelector('.page-head');
    const title = header?.querySelector('h1'), add = header?.querySelector('[data-team="new"]');
    require(visible(title) && visible(add), "team title and creation action are visible");
    const pageBox = page.getBoundingClientRect(), titleBox = title.getBoundingClientRect(), addBox = add.getBoundingClientRect();
    require(titleBox.left - pageBox.left >= 24 && pageBox.right - addBox.right >= 24, "team header uses the page's horizontal padding");
    require(Math.abs((titleBox.top + titleBox.bottom) / 2 - (addBox.top + addBox.bottom) / 2) <= 2 && titleBox.right < addBox.left, "title and Add share a header row");
    require(title.textContent === '團隊', "the member count is not glued to the title");
    return { check: name, padding: titleBox.left - pageBox.left };
  }
  if (name === "admin-approval" || name === "admin-approval-long" || name === "admin-approval-batch") {
    const card = [...document.querySelectorAll('.ask-card')].find(visible);
    require(card, "approval card is visible");
    const details = card.querySelector('.ask-q'), yes = card.querySelector('.ask-yes'), no = card.querySelector('.ask-no');
    const box = card.getBoundingClientRect();
    require(box.top >= 0 && box.bottom <= innerHeight, "complete card fits the viewport");
    require(visible(yes) && visible(no), "both approval actions remain visible");
    require(yes.getBoundingClientRect().right <= box.right && yes.getBoundingClientRect().bottom <= innerHeight, "approval action fits the card");
    require(no.getBoundingClientRect().right <= yes.getBoundingClientRect().left, "primary action stays rightmost");
    if (name === "admin-approval-batch") {
      const text = details.textContent;
      for (const member of ["COO", "PM", "RD", "CEO", "CTO", "Marketing", "Finance", "Design"]) {
        require(text.includes("名稱：" + member), "all eight names are preserved");
      }
      require((text.match(/職責：/g) || []).length === 8 && (text.match(/簡介：/g) || []).length === 8 && (text.match(/群組：/g) || []).length === 8, "every member includes role, blurb and group");
      require(!text.includes(": "), "Chinese field labels use full-width colons");
      require(document.querySelectorAll('.ask-card:not([hidden])').length === 1, "one card covers the whole batch");
    }
    if (name.endsWith("long") || name.endsWith("batch")) {
      if (name.endsWith("long")) require(details.textContent.includes("最後一項指示：保留完整決策紀錄。"), "full instructions are preserved");
      require(["auto", "scroll"].includes(getComputedStyle(details).overflowY), "long details can scroll");
      require(getComputedStyle(details).whiteSpace === "pre-wrap", "field line breaks remain readable");
      require(details.tabIndex === 0, "details can receive keyboard focus for scrolling");
      require(details.scrollTop > 0 && details.scrollTop + details.clientHeight >= details.scrollHeight - 1, "End key reveals the final instructions");
      require(yes.getBoundingClientRect().top >= details.getBoundingClientRect().bottom, "actions remain below the full-width details");
    }
    return { check: name, height: box.height, detailsHeight: details.clientHeight, contentHeight: details.scrollHeight };
  }
  if (name === "admin-created" || name === "admin-denied") {
    const calls = window.__look.calls, members = window.__look.data.members;
    require(calls.some(call => call.command === "agent_question_answer" && call.approved === (name === "admin-created")), "owner answer reaches the native boundary");
    require(members.some(member => member.id === 8) === (name === "admin-created"), "team reflects the owner's decision");
    if (name === "admin-created") {
      const details = [...document.querySelectorAll('.mb-det')].find(visible);
      require(details?.querySelector('.mb-who-name')?.value === "COO", "created member details are selected");
      require(details.querySelector('.mb-who-role')?.value === "營運長", "created role is shown");
      const created = members.find(member => member.id === 8);
      require(window.__look.data.groups.find(group => group.id === created.group_id)?.name === "個人公司", "member joins the approved group");
      require(created.preview === "" && created.last_at === null, "new conversation has no inherited activity");
      require(details.querySelector('.mb-av-placeholder svg'), "a member without a preset uses the default silhouette");
    }
    return { check: name, members: members.length };
  }
  if (name === "conversation-picker") {
    const list = document.querySelector("#app-popover .conversation-menu-list");
    require(visible(list), "conversation list is visible");
    require(list.scrollWidth <= list.clientWidth + 1, `list overflows: ${list.scrollWidth} > ${list.clientWidth}`);
    const button = list.querySelector("[data-conversation-new]"), box = button.getBoundingClientRect(), parent = list.getBoundingClientRect();
    require(box.left >= parent.left && box.right <= parent.right + 1, "new conversation button fits inside the list");
    return { check: name, width: list.clientWidth, scrollWidth: list.scrollWidth };
  }
  if (name === "settings-shell") {
    const modal = document.querySelector(".modal"), close = modal?.querySelector(".modal-x");
    require(visible(modal) && visible(close), "settings and its close control are visible");
    require(getComputedStyle(modal).borderTopLeftRadius === "12px", "settings uses the dialog radius rather than a pill");
    const box = close.getBoundingClientRect(), hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    require(close === hit || close.contains(hit), "rounded corners do not clip the close control");
    return { check: name, radius: getComputedStyle(modal).borderTopLeftRadius };
  }
  if (name === "dictation-navigation") {
    const nav = document.querySelector(".dictation-nav"), tabs = [...nav.querySelectorAll('[role="tab"]')];
    const active = tabs.filter(tab => tab.getAttribute("aria-selected") === "true");
    require(nav.getAttribute("aria-orientation") === "horizontal" && active.length === 1, "one selected horizontal tab");
    const panel = document.getElementById(active[0].getAttribute("aria-controls"));
    require(visible(panel) && !panel.hidden, "selected navigation displays its panel");
    const navBox = nav.getBoundingClientRect(), panelBox = panel.getBoundingClientRect();
    require(navBox.bottom < panelBox.top, "tabs remain above the content");
    for (let i = 1; i < tabs.length; i++) require(tabs[i].getBoundingClientRect().left >= tabs[i - 1].getBoundingClientRect().right, "tabs form a horizontal row");
    require(panel.scrollWidth <= panel.clientWidth + 1, "selected content fits its column");
    return { check: name, selected: active[0].dataset.dictationTab, panelWidth: panel.clientWidth };
  }
  if (name === "dictation-overview") {
    const cards = [...document.querySelectorAll('.page-overview .icard')];
    require(cards.length === 4, "four independent totals are visible");
    const boxes = cards.map(card => card.getBoundingClientRect());
    const desktop = document.querySelector('.dictation-content').clientWidth >= 760;
    require(desktop ? boxes.every(box => box.top === boxes[0].top) : boxes[2].top > boxes[0].bottom, "totals use one desktop row and two compact rows");
    for (const card of cards) require(getComputedStyle(card).borderTopWidth === '1px' && getComputedStyle(card).backgroundColor !== 'rgba(0, 0, 0, 0)', "each total has its own card surface");
    const activity = document.querySelector('.page-overview .activity').getBoundingClientRect();
    const shortcuts = document.querySelector('.page-overview .keys-card').getBoundingClientRect();
    require(activity.top >= boxes[3].bottom, "activity follows the totals");
    require(desktop ? shortcuts.left >= activity.right : shortcuts.top >= activity.bottom, "shortcuts sit alongside activity on desktop and below in compact windows");
    require(document.querySelector('.heat rect.h1, .heat rect.h2, .heat rect.h3, .heat rect.h4').getBoundingClientRect().width >= 17, "activity days retain larger readable cells");
    return { check: name, desktop, cardWidth: boxes[0].width };
  }
  if (name === "conversation-design") {
    const bubble = document.querySelector(".bubble"), answer = document.querySelector('[data-expandable] > .ans');
    for (const element of [bubble, answer]) {
      require(visible(element), "owner and assistant messages are visible");
      const style = getComputedStyle(element);
      require(style.fontSize === "15px" && style.lineHeight === "24px", "messages use 15px type at 1.6 line height");
      require(style.borderTopLeftRadius === "12px", "messages use the shared card radius");
    }
    require(getComputedStyle(answer).borderTopWidth === "0px", "assistant bubble has no decorative border");
    const column = document.querySelector(".mb-col");
    if (visible(column)) {
      const rows = [...column.querySelectorAll(".mb-row")], first = rows[0];
      require(first.dataset.member === "0", "Raphael is first");
      if (!column.classList.contains("is-collapsed")) {
        require(first.querySelector(".av").getBoundingClientRect().width >= 64, "Raphael retains his original large emblem");
        for (const row of rows.slice(1)) require(Math.abs(row.querySelector(".av").getBoundingClientRect().width - 40) < 1, "other members retain 40px faces");
      }
    }
    return { check: name, sidebarVisible: visible(column), fontSize: getComputedStyle(bubble).fontSize };
  }
  if (name === "member-column") {
    const column = document.querySelector('.mb-col'), toggle = column.querySelector('[data-col="collapse"]');
    const collapsed = column.classList.contains('is-collapsed');
    require(visible(column) && visible(toggle), "member column and toggle are visible at every width");
    require(toggle.getAttribute('aria-expanded') === String(!collapsed), "toggle reflects the column state");
    const rows = [...column.querySelectorAll('[data-member]')];
    require(rows[0].dataset.member === "0", "Raphael remains first");
    require(rows.filter(row => row.getAttribute('aria-current') === 'true').length === 1, "one active member is marked");
    if (collapsed) {
      require(Math.abs(column.getBoundingClientRect().width - 56) < 1, "collapsed column is 56px wide");
      require(localStorage.getItem('raphael.members.collapsed') === 'true', "collapsed choice is remembered");
      require(!visible(column.querySelector('.mb-chief b')), "only faces remain in the strip");
      for (const row of rows) require(row.dataset.tooltip === row.getAttribute('aria-label') && row.dataset.tooltip, "faces have localized name tooltips");
      require(visible(column.querySelector('.mb-dot.is-wait')), "waiting dot survives collapse");
    } else {
      require(rows[0].querySelector('.av').getBoundingClientRect().width >= 64, "expanded Raphael emblem retains its original size");
      require(!rows[0].querySelector('.mb-pin, time, .mb-row-p'), "Raphael retains the original emblem and name only");
    }
    return { check: name, collapsed, width: column.getBoundingClientRect().width, members: rows.length };
  }
  if (name === "usage-summary") {
    require(document.querySelectorAll(".run-tile").length === 6, "overview totals remain available");
    require(!document.querySelector(".run-list, .run-timeline, [data-run-id]"), "per-run navigation is absent");
    const tracks = [...document.querySelectorAll(".run-bar-track")];
    for (const track of tracks) {
      const fill = track.querySelector("i"), box = track.getBoundingClientRect();
      require(box.width > 0 && box.height === 7, "grouping and allowance tracks retain their visible height");
      require(visible(fill) && fill.getBoundingClientRect().height === 7, "bar fills remain visible");
    }
    return { check: name, bars: tracks.length };
  }
  if (name === "overlay-percentage") {
    const caption = document.querySelector("#progress");
    require(visible(caption) && /^\u300a \d+ % \u300b$/.test(caption.textContent), "original percentage caption is visible");
    require(Number(caption.textContent.match(/\d+/)[0]) > 0, "native progress advances beyond the initial zero");
    require(!document.body.textContent.includes("Transcribing") && !document.body.textContent.includes("\u8f49\u9304\u4e2d"), "elapsed-time captions are absent");
    return { check: name, caption: caption.textContent };
  }
  if (name === "member-details") {
    const panel = document.querySelector(".mb-det"), title = document.querySelector(".conversation-trigger");
    require(visible(panel), "details panel is visible");
    require(title.getAttribute("aria-expanded") === "true", "title reflects the panel state");
    require(title.getAttribute("aria-controls") === panel.id && !title.hasAttribute("aria-haspopup"), "title controls details without a popup");
    require([...title.querySelectorAll(".i")].every(icon => icon.closest(".mb-pill-face")), "title has no dropdown glyph outside its member avatar");
    const list = panel.querySelector(".conversation-list");
    require(visible(list) && list.scrollWidth <= list.clientWidth + 1, "conversation management fits the panel");
    return { check: name, width: list.clientWidth, scrollWidth: list.scrollWidth };
  }
  if (name === "composer-pair") {
    const form = [...document.querySelectorAll(".composer")].find(visible);
    const send = form.querySelector(".cmp-send"), voice = form.querySelector(".cmp-voice");
    const hasDraft = !!form.querySelector("textarea").value.trim();
    const shown = hasDraft ? send : voice, gone = hasDraft ? voice : send, a = shown.getBoundingClientRect();
    require(a.width === 32 && a.height === 32, "the one right-hand control is 32px");
    require(gone.getBoundingClientRect().width === 0, "send and the emblem share one slot: only one shows");
    require(!!voice.querySelector('img[src*="app-emblem"], .av') && !voice.querySelector('.av-custom'), "the voice button is the gold emblem, never a picture");
    require(getComputedStyle(shown).backgroundColor === "rgba(0, 0, 0, 0)", "the slot is a bare icon with no background");
    require(send.disabled === !hasDraft, "send state follows draft text");
    return { check: name, shown: a.width, hasDraft, color: getComputedStyle(send).color };
  }
  if (name === "calendar-chips") {
    const chips = [...document.querySelectorAll(".au-occurrence")];
    require(chips.length > 0, "calendar has event chips");
    for (const chip of chips) {
      const time = chip.querySelector("time, small"), face = chip.querySelector(".av"), label = chip.querySelector("b");
      const bounds = chip.getBoundingClientRect();
      if (bounds.width >= 110) require(visible(face), "member face is visible in a wide chip");
      if (visible(face)) {
        const picture = face.getBoundingClientRect();
        require(Math.abs(picture.width - 20) < 1 && Math.abs(picture.height - 20) < 1, "event faces retain their 20px square rather than flexing with the copy");
      }
      require(visible(time) && /^\d{2}:\d{2}$/.test(time.textContent), `complete compact time: ${time?.textContent}`);
      const range = document.createRange(); range.selectNodeContents(time);
      const text = range.getBoundingClientRect();
      require(text.left >= bounds.left + 1 && text.right <= bounds.right - 1 && text.top >= bounds.top && text.bottom <= bounds.bottom,
        `time ${time.textContent} fits chip (${Math.round(text.width)}px text, ${Math.round(bounds.width)}px chip)`);
      require(chip.title.includes(label.textContent) && chip.title.includes(time.textContent), "tooltip preserves full title and time");
      if (visible(label)) require(getComputedStyle(label).textOverflow === "ellipsis", "long visible title uses ellipsis");
      if (bounds.width <= 92) require(getComputedStyle(label).whiteSpace === "nowrap", "narrow calendar titles truncate on one line");
    }
    return { check: name, chips: chips.length };
  }
  throw new Error(`Unknown rendered check: ${name}`);
}
