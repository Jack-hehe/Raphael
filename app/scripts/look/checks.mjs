// Browser assertions run against the real app after the scene's user interactions.
export function checkRendered(name) {
  const visible = element => element && element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
  const require = (condition, message) => { if (!condition) throw new Error(`${name}: ${message}`); };
  // R-UI-42: system events are one centred grey line that never shows an internal number.
  const eventLines = expected => {
    const lines = [...document.querySelectorAll('.event-line')].filter(visible);
    require(lines.length === expected, `${expected} event lines are visible, found ${lines.length}`);
    for (const line of lines) {
      const box = line.getBoundingClientRect(), column = line.closest('.ra-col').getBoundingClientRect();
      require(Math.abs(box.left + box.width / 2 - column.left - column.width / 2) <= 2, `"${line.textContent}" is centred in the thread`);
      require(!/#\d/.test(line.closest('.handoff-event, .conversation-event-line, li').textContent), `"${line.textContent}" shows no internal number`);
      const size = parseFloat(getComputedStyle(line).fontSize);
      require(!line.closest('.chat-message, .ra-body') && size >= 12 && size <= 14, `event lines sit outside message bodies at a readable small size (${size}px)`);
      const control = line.closest('button');
      if (control) require(getComputedStyle(control).borderTopColor === 'rgba(0, 0, 0, 0)' && getComputedStyle(control).backgroundColor === 'rgba(0, 0, 0, 0)', "an event line is never a pill");
    }
    return lines;
  };
  const plainWords = () => {
    const wrapped = /^\s*《(?:答|告|否|了|問|解|Answer|Notice|Negative|Understood|Question)[。.：:]/i;
    const words = [...document.querySelectorAll('.page-agent .ra .message-text, .page-agent .work-latest, .page-agent .work-progress, .page-agent .ask-q, .page-agent .bgq-line')].filter(visible);
    for (const text of words) require(!wrapped.test(text.textContent) && !/》\s*$/.test(text.textContent.replace(/《[^《》]*》/g, "")), `R-UI-42: Raphael's words read plainly: ${text.textContent.slice(0, 40)}`);
    return words.length;
  };
  if(name.startsWith("p04-")) {
    const messages=[...document.querySelectorAll('.au-room-thread .message-text')];
    if(name==='p04-blocked'){require(document.querySelector('[data-handoff]').textContent.includes('缺少驗收所需的檔案'),'missing artifact reason is actionable');require(!document.querySelector('[data-handoff] .message-quote'),"the request is only ever the delegating member's own message");}
    if(name==='p04-chief-last')require(document.querySelector('.au-room-thread .tn:last-child .au-room-wrapup') || document.querySelector('.au-room-thread .tn:last-child.au-room-wrapup'),'chief is last');
    if(name==='p04-status-only')require(!messages.some(message=>message.textContent.includes('正在發言')),'progress never enters final bodies');
    if(name==='p04-addresses')require(!messages.some(message=>/@(?:\s|$)/u.test(message.textContent)),'no dangling address');
    if(name==='p04-short')require(document.querySelector('.au-room-wrapup').textContent.includes('沒有可驗收的成果'),'zero results are explicit');
    if(name==='p04-task-slot'){
      const form=document.querySelector('.composer[data-task-state="running"]');
      require(visible(form.querySelector('.cmp-voice'))&&!visible(form.querySelector('.cmp-send')),'only Stop occupies the slot');
      require(form.querySelector('.cmp-voice').getAttribute('aria-label')==='停止','Stop is accessible');
    }
    return {check:name,matched:true};
  }
  if (name.startsWith("p02-")) {
    const data = window.__look.data, calls = window.__look.calls;
    if (name === "p02-roster") require(calls.some(c => c.method === "members.list"), "shared roster boundary is read");
    if (name === "p02-mentions" || name === "p02-room-mentions") {
      const menu = document.querySelector("#app-popover");
      require(visible(menu), "mention menu is visible");
      require(menu.textContent.includes(data.members[1].name), "bot comes from the roster");
      require(data.servers.some(app => menu.textContent.includes(app.name)), "app comes from the same snapshot");
      const rows = [...menu.querySelectorAll('button')], row = label => rows.find(item => item.querySelector('.mt').textContent === label);
      for (const item of rows) require(visible(item.querySelector('.mlead > *')), `${item.textContent} leads with a face or a mark`);
      require(row("@" + data.members[1].name)?.querySelector('.mlead .av'), "a bot shows its member face");
      for (const app of data.servers) require(row("@" + app.name)?.querySelector('.mlead .mono'), `${app.name} shows its connection mark`);
      const columns = selector => rows.map(item => item.querySelector(selector).getBoundingClientRect().left);
      for (const selector of ['.mlead', '.mt']) require(Math.max(...columns(selector)) - Math.min(...columns(selector)) <= 1, `${selector} aligns in one column`);
    }
    if (name === "p02-picker") require(document.querySelectorAll(".room-picked span").length === 2, "Raphael and the selected colleague have chips");
    if (name === "p02-fallback") require(document.querySelector(".au-room-controls").textContent.includes("群組預設"), "fallback is visible");
    if (name === "p02-blocked") {
      const lines = eventLines(3), rows = [...document.querySelectorAll('.au-room-thread > li')];
      require(lines.every(line => line.closest('.au-room-thread > li.room-intent-blocked')), "blocked replies are lines of the thread");
      require(!document.querySelector('.au-room-controls')?.textContent.trim(), "nothing about blocked replies sits above the composer");
      const at = selector => rows.indexOf(document.querySelector(selector)?.closest('li'));
      const blocked = rows.filter(row => row.matches('.room-intent-blocked')).map(row => rows.indexOf(row));
      require(blocked[0] === at('[data-room-message="2"]') + 1 && blocked[1] === at('[data-room-message="7"]') + 1 && blocked[2] === blocked[1] + 1, "each blocked reply stands where its exchange stopped");
      require(lines[0].textContent === "小森 未能回覆：請求太頻繁而被限流" && lines[1].textContent === "小晴 未能回覆：已停止", "blocked replies name the member and why in the window language");
    }
    if (name === "p02-meeting-event") {
      const [line] = eventLines(1);
      require(line.closest('.conversation-event-line [data-meeting-room]') && line.textContent === "已開啟會議 產品方向討論", "the meeting is one centred line that opens its room");
      require(visible(line.querySelector('svg.i')) && !line.closest('.ra'), "a small group icon leads the line, beside no avatar");
    }
    if (name === "p02-meeting-open") require(document.querySelector(".au-room-heading b").textContent === data.rooms[0].name, "meeting link opens the real room pane");
    if (name === "p02-roles") require(calls.some(c => c.method === "rooms.update" && c.params.roles?.[1] === "moderator"), "room roles are persisted at the native boundary");
    if (name === "p02-long-exchange") {
      const exchange = data.roomExchanges.at(-1);
      require(exchange.hops > 3 && exchange.turns_taken > 6, "rendered exchange crosses both admission limits");
      require(exchange.reply_intents.every(intent => intent.state === "done"), "wrap-up has no pending intent");
    }
    return { check: name, matched: true };
  }
  if (name === "admin-description-draft") {
    const input = document.querySelector('.composer textarea');
    require(visible(input) && input.value.includes("整理未讀信並準備回覆"), "description opens the chief's ordinary composer");
    require(input.value.includes("確認後才建立"), "description requests an editable proposal");
    require(!window.__look.calls.some(call => call.method === 'members.create'), "description does not create a member directly");
    return { check: name };
  }
  if (name === "first-bot-choice" || name === "first-bot-free-text") {
    const card = document.querySelector('.first-bot:not([hidden])');
    require(visible(card), "first Bot conversation card is visible");
    require(card.querySelectorAll('[data-first-choice]').length === 2, "two suggested answers");
    require(visible(card.querySelector('textarea')), "free text answer is available");
    require(window.__look.data.members.length === 1 && window.__look.data.members[0].id === 0, "one stable first Bot");
    require(!window.__look.calls.some(call => call.command === 'agent_ask_text'), "choices have no side effects");
    if (name === "first-bot-free-text") require(card.querySelector('textarea').value === "星河", "Back preserves free text");
    return { check: name, text: card.querySelector('textarea').value };
  }
  if (name === "first-bot-draft") {
    const draft = document.querySelector('.draft-panel:not([hidden])');
    require(visible(draft) && draft.querySelectorAll('.draft-card').length === 1, "first Bot uses the shared draft");
    require(draft.querySelector('[data-draft-field="name"]').value === "晨星", "selected name reaches the draft");
    require(window.__look.data.members[0].profile_ready === false, "profile is not saved before confirmation");
    const sent = window.__look.calls.find(call => call.command === 'agent_ask_text');
    require(sent && !/member_update|member ID|成員 ID|Bot/.test(sent.text), "setup request uses natural window-language words");
    return { check: name };
  }
  if (name === "bot-renamed-centred") {
    const lines = [...document.querySelectorAll('.bot-renamed')].filter(visible);
    require(lines.length === 1 && lines[0].textContent === "已重新命名為 晨星", "one saved rename line");
    require(getComputedStyle(lines[0]).textAlign === "center", "rename line is centered");
    require(window.__look.data.members.length === 1 && window.__look.data.members[0].profile_ready, "profile saved without a second Bot");
    const answers = window.__look.calls.filter(call => call.command === 'agent_question_answer');
    require(answers.length === 1 && answers[0].drafts[0].member_id === 0, "shared apply boundary runs once");
    return { check: name };
  }
  if (name === "admin-draft-cancel" || name === "admin-edited-result") {
    const calls = window.__look.calls.filter(call => call.command === 'agent_question_answer');
    require(calls.length === 1 && calls[0].approved === (name === "admin-edited-result"), "one owner decision");
    const created = window.__look.data.members.filter(member => member.id >= 8);
    require(created.length === (name === "admin-edited-result" ? 2 : 0), "only selected drafts are applied");
    if (name === "admin-edited-result") {
      require(created[0].role === "首席營運長" && !created.some(member => member.name === "RD"), "edits and removal persist");
      require(calls[0].groups[0].name === "工作室", "edited group crosses the confirmation boundary");
      require(created.every(member => window.__look.data.groups.some(group => group.id === member.group_id && group.name === "工作室")), "edited group membership persists");
    }
    return { check: name, created: created.length };
  }
  if (name === "plain-bot-chat") {
    const replies = [...document.querySelectorAll('.tn[data-id="1"] .ra [data-expandable] .message-text')].filter(visible);
    require(replies.length === 2, "the core's two-message reply shows as two answers");
    require(replies[0].textContent.startsWith("這週的研究有三個重點") && replies[1].textContent.trim().endsWith("方便你接著討論。"), "the reply loses only its opening and closing marks");
    return { check: name, words: plainWords() };
  }
  if (name === "centred-system-events") {
    const lines = eventLines(3).map(line => line.textContent);
    require(lines.join("|") === "已轉交給 小晴已完成|已詢問 小森執行中|已開啟會議 產品方向討論", `hand-off, consultation and meeting lines in order: ${lines.join("|")}`);
    const answer = document.querySelector('.tn[data-id="1"] .ra').getBoundingClientRect(), last = document.querySelector('.tn[data-id="1"] .conversation-event-line').getBoundingClientRect();
    require(last.bottom <= answer.top, "events stand between the owner's words and Raphael's answer");
    return { check: name, lines };
  }
  if (name === "conversation-running") {
    const calls = window.__look.calls.filter(call => call.method === "agent.history.list");
    require(calls.some(call => (call.params?.member_id ?? 0) === 0 && (call.params?.conversation_id ?? 1) === 1), "R-UI-32: running turn belongs to the open formal conversation");
    require([...document.querySelectorAll('.ra .work-toggle')].some(visible), "R-UI-32: running progress is visible");
    require(![...document.querySelectorAll('.ra .chat-message[data-expandable]')].some(visible), "R-UI-32: completed answer is absent while running");
    require(document.querySelector('.work-latest')?.textContent === "已核對三份報告，正在彙整研究結論。", "R-UI-42: the running state shows the latest step plainly");
    return { check: name, running: true, words: plainWords() };
  }
  if (name === "live-scenario-owner-view") {
    const answers = [...document.querySelectorAll('.ra [data-expandable] > .ans')].filter(visible);
    require(answers.length === 1, "one owner-visible terminal answer");
    require(answers[0].textContent.includes("需要重新登入並授予讀取權限。"), "actionable zh-TW failure survives the real renderer");
    require(!answers[0].textContent.includes("正在查看"), "progress stays outside final messages");
    const calls = window.__look.calls.filter(call => call.method === "agent.history.list");
    require(calls.some(call => (call.params?.member_id ?? 0) === 0), "owner view reads Raphael's production RPC scope");
    return { check: name, answers: answers.length, scope: "personal", status: "missing-connection" };
  }
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
  if (name === "room-required-chief") {
    const chief = document.querySelector('[data-dlg="member"][data-member="0"]');
    require(chief?.disabled && chief.getAttribute('aria-pressed') === 'true', 'Raphael is required');
    return { check: name, chief: 0 };
  }
  if (name === "room-generated-name" || name === "room-edited-name") {
    const input = document.querySelector('#room-name');
    require(name === 'room-edited-name' ? input.value === '上線協作' : input.value.includes('小晴') && input.value.includes('拉斐爾'), 'generated name follows selection and preserves owner edits');
    return { check: name, value: input.value };
  }
  if (name === 'room-isolation-send') {
    require(window.__look.calls.some(c => c.method === 'rooms.send' && c.params.text === 'roomnonce-private-isolation'), 'room send reaches its own boundary');
    require(!window.__look.calls.some(c => c.method === 'agent.ask'), 'room send never uses private ask');
    require(!document.querySelector('[data-room-act="turn"]'), 'room messages have no private navigation');
    return { check: name };
  }
  if (name === 'room-private-isolation') {
    require(!document.querySelector('.mb-chat:not([hidden]) .thread')?.textContent.includes('roomnonce-private-isolation'), 'private history excludes room text');
    return { check: name };
  }
  if (['room-reply-owner','room-reply-peer','room-reply-source-missing'].includes(name)) {
    const id = name === 'room-reply-owner' ? 2 : 3;
    const chip = document.querySelector(`[data-room-message="${id}"] .au-room-reply`);
    require(visible(chip), 'source chip is visible');
    require(chip.textContent === (name === 'room-reply-owner' ? '回覆 你' : name === 'room-reply-peer' ? '回覆 小晴' : '來源訊息已無法取得'), 'chip uses server source identity');
    return { check: name, source: chip.textContent };
  }
  if (name === "quick-model-row") {
    const row = document.querySelector('[data-row="quick-model"]');
    const picker = row?.querySelector('.mp-trigger');
    require(visible(picker) && !!picker.querySelector('.mp-tname')?.textContent && !!picker.querySelector('.mp-source')?.textContent, "quick model uses the shared named provider card");
    require(row.querySelector('.models-purpose-note')?.textContent.includes("Alt+Space"), "quick questions describe the shortcut's purpose");
    require(!!row.querySelector('[data-price="quick-model"]')?.textContent, "quick questions show the effective model price");
    const effort = row.querySelector('[data-k="quick-effort"]');
    require(visible(effort) && Math.abs(picker.getBoundingClientRect().right - effort.getBoundingClientRect().right) < 2, "effort aligns under the model card");
    return { check: name };
  }
  if (name === "quick-conversation-list") {
    const first = document.querySelector('.mb-det [data-conversation-open]');
    require(first?.dataset.conversationOpen === "4", "quick questions are pinned first");
    require(first?.textContent.includes("快速提問"), "the kind uses the window-language title");
    return { check: name, conversation: 4 };
  }
  if (name === "quick-conversation") {
    require(document.querySelector('.conversation-trigger')?.textContent.includes("快速提問"), "the opened conversation keeps its kind title");
    require(document.querySelector('.tn[data-id="90"] .me')?.textContent.includes("今天下午有哪些會議"), "the saved quick question is visible");
    require(!document.querySelector('.tn[data-id="1"]'), "formal history stays out of quick questions");
    return { check: name, conversation: 4 };
  }
  if (name === "quick-question-result") {
    const turn = document.querySelector('.tn[data-id="91"]');
    require(visible(turn) && turn.querySelector('.me')?.textContent.includes("明天早上要帶傘嗎"), "the Alt+Space question arrived in the open quick conversation");
    require(visible(turn.querySelector('.ra [data-expandable]')) && turn.querySelector('.ra [data-expandable]').textContent.includes("出門記得帶傘"), "its answer shows below its public step");
    const meta = turn.querySelector('.ra-face')?.getAttribute('title') ?? "";
    require(/luna/i.test(meta) && !/\bsol\b/i.test(meta), `the answer names the quick model that answered it: ${meta}`);
    require(document.querySelector('.conversation-trigger')?.textContent.includes("快速提問"), "the quick conversation stays open");
    require(!document.querySelector('.tn[data-id="1"]'), "formal history stays out of quick questions");
    return { check: name, model: meta, words: plainWords() };
  }
  if (name === "quick-formal-isolation-formal") {
    require(visible(document.querySelector('.tn[data-id="1"]')), "Raphael's formal thread is open");
    require(!document.querySelector('.tn[data-id="90"]'), "quick questions stay out of the formal thread");
    return { check: name };
  }
  if (name === "quick-formal-isolation") {
    const sent = window.__look.calls.filter(call => call.command === "agent_ask_text");
    require(sent.length === 2, `two typed questions reached the native boundary, found ${sent.length}`);
    require(sent[0].text === "整理這週的研究重點" && sent[0].conversationId === 1 && !("destination" in sent[0]), "the formal question names its thread and no quick destination");
    require(sent[1].text === "那明天呢？" && sent[1].conversationId === 4 && sent[1].destination === "quick", "the quick follow-up names the quick destination and its conversation");
    require(visible(document.querySelector('.tn[data-id="90"]')) && !document.querySelector('.tn[data-id="1"]'), "the quick thread shows only quick questions");
    return { check: name, sent: sent.map(call => ({ conversation: call.conversationId, destination: call.destination ?? null })) };
  }
  if (name === "quick-stop") {
    const stops = window.__look.calls.filter(call => call.method === "agent.history.stop");
    require(stops.length === 1 && stops[0].params.session_id === "look-quick" && stops[0].params.member_id === 0, "Stop names the running quick request alone");
    const turn = document.querySelector('.tn[data-id="90"]');
    require(turn?.dataset.status === "denied" && !turn.dataset.live, "the quick question ended where it ran");
    require(!document.querySelector('.composer[data-task-state="running"]'), "the gold slot is free again");
    require(window.__look.data.turns.find(item => item.id === 1)?.status === "done", "the formal thread is untouched");
    return { check: name };
  }
  if (name === "quick-model-selection") {
    const sets = window.__look.calls.filter(call => call.method === "settings.set").map(call => call.params);
    require(sets.some(set => set.key === "quick_model" && set.value === "cli:codex:gpt-6.1"), "the quick model is saved as the subscription model, its effort apart");
    require(sets.some(set => set.key === "quick_effort" && set.value === "high"), "the quick effort is saved on its own");
    require(!sets.some(set => set.key === "agent_model") && window.__look.data.settings.agent_model === "openai/gpt-6-sol", "Raphael's answer model stays as it was");
    const row = document.querySelector('[data-row="quick-model"]');
    require(row.querySelector('[data-k="quick-model"]')?.getAttribute('aria-label')?.includes("GPT-6.1"), "the row shows the chosen model");
    require(row.querySelector('[data-price="quick-model"]')?.textContent.trim() === "訂閱", "a subscription model shows it is billed by subscription");
    return { check: name, saved: sets.filter(set => set.key.startsWith("quick_")) };
  }
  if (name === "overlay-step") {
    require(document.getElementById('progress')?.textContent === "《告。正在查看今天下午的行程。》", "the latest shared turn narration is the subtitle");
    return { check: name, text: document.getElementById('progress').textContent };
  }
  if (name.startsWith("model-choice-")) {
    const dialog = document.querySelector('.model-chooser');
    const scroller = dialog?.querySelector('.mc-scroll');
    require(visible(scroller), "the model list is visible");
    const rows = [...dialog.querySelectorAll('.mc-row, .lm-row')];
    require(rows.length > 0, "the catalogue contains model choices");
    const viewport = scroller.getBoundingClientRect();
    if (name === "model-choice-scroll") {
      const last = rows.at(-1).getBoundingClientRect();
      require(last.bottom <= viewport.bottom + 1 && last.top >= viewport.top - 1, "the final model can be reached inside the list");
      return { check: name, rows: rows.length };
    }
    if (name === "model-choice-selection") {
      const selected = dialog.querySelector('.lm-main[aria-checked="true"]');
      require(selected?.dataset.name === 'qwen3-asr', "the preview selects exactly the requested local model");
      require(dialog.querySelectorAll('.lm-main[aria-checked="true"]').length === 1, "only one local radio is selected");
      require(document.activeElement === selected && parseFloat(getComputedStyle(selected).outlineWidth) >= 2, "keyboard focus stays visible on the choice");
      const mark = selected.querySelector('.ck svg');
      require(visible(mark) && mark.getBoundingClientRect().width <= 16 && getComputedStyle(mark).fill === 'none' && getComputedStyle(mark).stroke !== 'none', "the selected model has a bounded, visible check mark");
      require(!window.__look.calls.some(call => ['models.download', 'models.use', 'settings.set'].includes(call.method)), "previewing does not download or save settings");
      require(getComputedStyle(selected.closest('.lm-row')).backgroundColor !== getComputedStyle(rows[1]).backgroundColor, "selection has a distinct themed surface");
      return { check: name, selected: selected.dataset.name };
    }
    let minimumGap = Infinity;
    for (let i = 0; i < rows.length; i++) {
      const box = rows[i].getBoundingClientRect(), style = getComputedStyle(rows[i]);
      require(box.left >= viewport.left && box.right <= viewport.right + 1, "model choices fit the scroll pane");
      require(style.borderTopStyle === 'solid' && parseFloat(style.borderTopWidth) <= 1, "choices use subtle designed borders instead of native button frames");
      const button = rows[i].matches('.lm-row') ? rows[i].querySelector('.lm-main') : rows[i];
      const buttonStyle = getComputedStyle(button);
      require(!['outset', 'inset'].includes(buttonStyle.borderTopStyle), "model buttons reset the browser's default border");
      require(parseFloat(buttonStyle.paddingTop) >= 12 && box.height >= 80, "each choice retains breathing room and a comfortable target");
      for (const text of button.querySelectorAll('.mp-line, .mp-sub, .mc-description')) {
        require(text.scrollWidth <= text.clientWidth + 1, "model metadata wraps within its column");
      }
      if (i > 0) minimumGap = Math.min(minimumGap, box.top - rows[i - 1].getBoundingClientRect().bottom);
    }
    require(rows.length < 2 || minimumGap >= 8, "adjacent model choices have at least eight pixels of separation");
    const footer = dialog.querySelector('.mb-dlg-act').getBoundingClientRect();
    require(viewport.bottom <= footer.top + 1, "scrolling choices cannot cover the fixed actions");
    return { check: name, rows: rows.length, minimumGap: Number.isFinite(minimumGap) ? minimumGap : null };
  }
  if (name === "delete-confirmation") {
    const dialogs = [...document.querySelectorAll('.mb-dialog.mb-confirm')];
    const dialog = dialogs.at(-1);
    require(visible(dialog), "the confirmation is visible");
    const buttons = [...dialog.querySelectorAll('.mb-dlg-act .btn')];
    require(buttons.map(button => button.dataset.dlg).join('|') === 'cancel|delete', "Cancel precedes Delete visually and in keyboard order");
    require(document.activeElement === buttons[0], "Cancel receives initial focus");
    require(visible(document.getElementById(dialog.getAttribute('aria-describedby'))), "the irreversible-action description is announced");
    require(buttons[1].classList.contains('pri'), "Delete uses the skin's primary accent");
    require(getComputedStyle(dialog.querySelector('.mb-dlg-act')).borderTopWidth === '0px', "no divider separates the compact footer");
    require(parseFloat(getComputedStyle(buttons[0]).borderRadius) >= 18, "actions have pill corners");
    const box = dialog.getBoundingClientRect();
    const close = dialog.querySelector('[data-dlg="close"]').getBoundingClientRect();
    require(box.width <= 482 && box.left >= 0 && box.right <= innerWidth, "the compact panel fits the window");
    require(close.width >= 32 && close.height >= 32, "the close control retains its hit target");
    return { check: name, width: box.width, height: box.height };
  }
  if (name.startsWith("cli-account-")) {
    const row = document.querySelector('[data-cli-menu="codex"], [data-cli="codex"]')?.closest('.cli-card');
    require(visible(row), "the Codex card is visible");
    const calls = window.__look.calls;
    if (name === "cli-account-menu") {
      require(visible(document.querySelector('#app-popover.mb-menu [data-pick="disconnect"]')), "disconnect uses the shared row menu");
      require(!document.querySelector('select'), "no native select is introduced");
    } else {
      require(calls.filter(call => call.method === 'brains.cli_disconnect' && call.params.id === 'codex').length === 1, "one menu choice disconnects exactly once without confirmation");
      const cli = window.__look.data.clis.find(cli => cli.id === 'codex');
      require(cli.logged_in === true, "the CLI's own login remains intact");
      if (name === "cli-account-disconnected") {
        require(cli.disconnected && !cli.available && cli.models.length === 0, "disconnected CLI supplies no runnable models");
        require(visible(row.querySelector('[data-cli="codex"]')), "reconnection stays on the card");
        require(!row.querySelector('[data-cli-menu]'), "a disconnected card has no connected menu");
      } else {
        require(visible(row.querySelector('[data-cli-menu="codex"]')) && cli.available && !cli.disconnected, "reconnection restores the connected card");
        require(calls.filter(call => call.method === 'brains.cli_login' && call.params.id === 'codex').length === 1, "connect reaches the core once");
      }
    }
    return { check: name, text: row.textContent };
  }
  if (name === "room-discussion") {
    require(document.querySelectorAll('.au-room-reply').length >= 3, "multiple replies identify their colleagues");
    require(document.querySelector('.au-room-thread').textContent.includes('我不同意'), "members can disagree");
    require(visible(document.querySelector('.au-room-crown')), "Raphael has the chief badge");
    const crown = document.querySelector('.au-room-crown').getBoundingClientRect();
    const face = document.querySelector('.au-room-crown').parentElement.getBoundingClientRect();
    require(crown.right <= face.right && crown.left >= face.left, "the chief badge stays inside its participant slot");
    // Messages carry face, name and reply chip only; roles stay with the faces of the header and the member column.
    require(document.querySelectorAll('.au-room-participant[data-tooltip*=" · "]').length === 3, "header faces name each member and role");
    for (const meta of document.querySelectorAll('.au-room-meta')) require([...meta.children].every(child => child.matches('.au-room-author, .au-room-reply')), "no role tag above a message");
    return { check: name, replyChips: document.querySelectorAll('.au-room-reply').length };
  }
  if (name === "room-wrapup") {
    const card = document.querySelector('.au-room-wrapup');
    require(visible(card), "the wrap-up is visible");
    const answer = getComputedStyle(card.querySelector('.ans')), ordinary = getComputedStyle(document.querySelector('.au-room-message:not(.au-room-wrapup) .ra .ans'));
    require(answer.borderLeftWidth === '0px' && answer.borderTopWidth === '0px' && answer.backgroundColor === ordinary.backgroundColor && answer.paddingLeft === ordinary.paddingLeft, "the wrap-up is Raphael's ordinary message, not a bordered card");
    require(card.querySelector('.au-room-author')?.textContent === '拉斐爾' && visible(card.querySelector('.ra-face .av')) && !/討論收束/.test(card.textContent), "face and name lead the wrap-up like every room message");
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
    require(!document.querySelector('[data-room-act="stop"]'), "no separate Stop pill remains");
    require(!document.querySelector('.au-room-pane .composer[data-task-state="running"]'), "terminal snapshot releases Stop");
    return { check: name, stopRequested: true };
  }
  if (name === "room-replies") {
    const chip = document.querySelector('[data-room-message="3"] .au-room-reply');
    require(visible(chip) && chip.textContent === "回覆 小晴", "the reply identifies its recipient");
    require(chip.getBoundingClientRect().right <= innerWidth, "the reply chip fits the viewport");
    return { check: name, recipient: 1 };
  }
  if (name === "room-thinking") {
    const exchange = window.__look.data.roomExchanges.at(-1);
    require(exchange.active_turn === 2 && exchange.reply_intents.filter(intent => intent.state === "running").length === 1, "the fixture matches one active core turn and queued reply intents");
    const rows = [...document.querySelectorAll('.au-room-thinking')];
    require(rows.length === 1 && visible(rows[0]), "only one member is shown speaking");
    require(getComputedStyle(rows[0]).flexDirection === 'row', "speaker face and status share one row");
    require(visible(rows[0].querySelector('.av')), "the speaker's face is visible");
    require(rows[0].textContent === "小晴 正在發言…", "the speaking row names the member");
    return { check: name, speakingRows: rows.length };
  }
  if (name === "p03-handoff" || name === "p03-handoff-expanded") {
    const task = window.__look.data.roomHandoffs[0], event = document.querySelector(`[data-handoff="${task.task_id}"]`);
    require(visible(event) && document.querySelectorAll('[data-handoff]').length === 1, "one durable receipt is visible");
    require(!event.closest('.chat-message'), "handoffs are events outside message bodies");
    require(event.dataset.state === task.state, "receipt and rendered state agree");
    require(event.querySelector('summary').textContent.startsWith('已轉交給 小晴'), "the receipt names its assignee");
    require(visible(event.querySelector('summary .av')), "the compact line includes the member face");
    eventLines(1);
    require(task.ready_at_ms === task.created_at_ms && task.dispatch_policy === 'immediate', "source dates do not schedule work");
    if (name === "p03-handoff-expanded") {
      const detail = event.querySelector('.handoff-detail');
      require(event.open && visible(detail.querySelector('.message-text')), "expansion shows the persisted result");
      require(detail.querySelector('.message-quote')?.textContent === "你請小晴立即整理驗收報告。", "expansion shows what was asked");
      require(detail.querySelector('.handoff-files')?.textContent === "上線驗收報告.html", "expansion names the produced file, not its digest");
      require(!/#\d|digest|fixture-report/.test(detail.textContent), "expansion shows no internal ids");
    } else require(!event.open, "handoff details start collapsed");
    return { check: name, taskId: task.task_id, state: task.state, readyAt: task.ready_at_ms };
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
      const lead = item.querySelector('.mlead'), first = lead ?? item.querySelector('.mt'), itemBox = item.getBoundingClientRect();
      require(first && first.getBoundingClientRect().left - itemBox.left <= 12 && (!lead || lead.childElementCount > 0), "a completion starts at the row padding with its face, mark or text");
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
    // Each page is named on one line in the sidebar, at every width: 連結應用程式 and its number must fit the narrow sidebar too.
    for (const label of document.querySelectorAll('#nav .nav .nav-t')) if (visible(label))
      require(label.getBoundingClientRect().height < (parseFloat(getComputedStyle(label).lineHeight) || 20) * 1.5, `"${label.textContent}" does not fit on one line in the sidebar`);
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
  if (name === "member-draft" || name === "member-draft-edit") {
    const panel = [...document.querySelectorAll('.draft-panel')].find(visible);
    require(panel, "draft panel is visible");
    require(!document.querySelector('.ask-card:not([hidden])'), "plain ask card is hidden when draft panel is shown");
    const box = panel.getBoundingClientRect();
    require(box.top >= 0 && box.bottom <= innerHeight, "draft panel fits the viewport");
    const cards = [...panel.querySelectorAll('.draft-card')].filter(visible);
    const confirmBtn = panel.querySelector('[data-draft-act="confirm"]');
    const cancelBtn = panel.querySelector('[data-draft-act="cancel"]');
    require(visible(confirmBtn) && visible(cancelBtn), "confirm and cancel buttons are visible");
    require(cancelBtn.getBoundingClientRect().right <= confirmBtn.getBoundingClientRect().left, "primary action stays rightmost");
    for (const card of cards) {
      const roleInput = card.querySelector('[data-draft-field="role"]');
      const nameInput = card.querySelector('[data-draft-field="name"]');
      const groupBtn = card.querySelector('[data-draft-act="group"]');
      const roleStyle = getComputedStyle(roleInput);
      const roleContentWidth = roleInput.clientWidth - parseFloat(roleStyle.paddingLeft) - parseFloat(roleStyle.paddingRight);
      const roleFontSize = parseFloat(roleStyle.fontSize);
      require(roleContentWidth >= roleFontSize * 8, `role field fits at least 8 CJK characters (${roleContentWidth}px vs ${roleFontSize * 8}px)`);
      require(roleInput.scrollWidth <= roleInput.clientWidth + 1, `role value is not clipped (${roleInput.value})`);
      require(nameInput.clientWidth >= roleFontSize * 4.5, "name field has enough width");
      require(groupBtn.getBoundingClientRect().width < roleInput.getBoundingClientRect().width, "group dropdown is narrower than role field");
    }
    if (name === "member-draft") {
      require(cards.length === 3, "three member draft cards are shown");
      require(confirmBtn.textContent.trim() === "建立 3 位成員", "primary button shows three members to create");
      const names = cards.map(c => c.querySelector('[data-draft-field="name"]')?.value);
      require(names.join(",") === "COO,PM,RD", "all three member draft names are populated");
    } else {
      require(cards.length === 2, "removing one draft leaves two cards");
      require(confirmBtn.textContent.trim() === "建立 2 位成員", "primary button updates count to two members");
      require(cards[0].querySelector('[data-draft-field="role"]')?.value === "首席營運長", "edited role field is preserved");
    }
    return { check: name, cards: cards.length, confirmLabel: confirmBtn.textContent.trim() };
  }
  if (name === "member-draft-submitted") {
    const calls=window.__look.calls.filter(call=>call.command==='agent_question_answer');
    require(calls.length===1&&calls[0].approved&&calls[0].drafts?.length===3,'confirmation submits the edited draft once');
    const turn=window.__look.data.turns.find(turn=>turn.status==='done'&&turn.detail?.includes('COO'));
    require(turn&&turn.text.startsWith('已建立'),'native boundary persists the settled draft');
    return {check:name,turnId:turn.id,conversationId:turn.conversation_id};
  }
  if (name === "member-draft-done") {
    const done = [...document.querySelectorAll('.draft-done')].find(visible);
    require(done, "settled one-line summary is visible");
    require(!document.querySelector('.draft-panel:not([hidden])'), "draft panel is closed after confirmation");
    const sumBtn = done.querySelector('[data-act="draft-done"]');
    require(visible(sumBtn) && sumBtn.textContent.includes("已建立 COO、PM、RD"), "settled summary lists created members");
    const list = done.querySelector('.draft-done-list');
    require(visible(list) && list.querySelectorAll('.draft-done-item').length === 3, "expanded summary shows created member details");
    const members = window.__look.data.members;
    for (const createdName of ["COO", "PM", "RD"]) {
      require(members.some(m => m.name === createdName), `${createdName} was added to members`);
    }
    const column = document.querySelector(".mb-col");
    if (visible(column)) {
      const colText = column.textContent + " " + [...column.querySelectorAll("[aria-label]")].map(el => el.getAttribute("aria-label")).join(" ");
      for (const createdName of ["COO", "PM", "RD"]) {
        require(colText.includes(createdName), `${createdName} appears in the left member column`);
      }
    }
    return { check: name, summary: sumBtn.textContent.trim(), members: members.length };
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
  if (name === "apps-count") {
    // One count from one inventory: the sidebar's badge, the number beside 已連結, the rows under it and the roster every model is given.
    const badge = document.querySelector("#nav .nav-apps .nav-count"), tab = document.querySelector('.modal-scrim [data-conn="tab"][data-id="installed"] .n');
    require(visible(badge) && visible(tab), "the sidebar's badge and the tab's number are visible");
    const rows = document.querySelectorAll('.modal-scrim .cs-body [data-stagger="installed"] > .cs-item').length;
    return window.__TAURI_INTERNALS__.invoke("core_call", { method: "members.list", params: {} }).then(({ team_context: roster }) => {
      require(badge.textContent.trim() === tab.textContent.trim(), `the sidebar says ${badge.textContent} and the tab ${tab.textContent}`);
      require(String(roster.connected) === badge.textContent.trim(), `the roster says ${roster.connected} and the sidebar ${badge.textContent}`);
      require(String(rows) === badge.textContent.trim(), `${rows} rows are listed as connected, the count is ${badge.textContent}`);
      require(roster.apps.filter(app => app.connected).length === roster.connected, "the roster counts the apps it lists as connected");
      return { check: name, count: roster.connected, apps: roster.apps.length };
    });
  }
  if (name === "apps-unreadable") {
    // What cannot be known is 「—」 wherever the number would be, never a guess: the sidebar, the tab, and the roster every model is given.
    const entry = document.querySelector("#nav .nav-apps"), badge = entry?.querySelector(".nav-count"), tab = document.querySelector('.modal-scrim [data-conn="tab"][data-id="installed"] .n');
    require(visible(badge) && visible(tab), "the sidebar's badge and the tab's number are visible");
    require(badge.textContent.trim() === "—" && tab.textContent.trim() === "—", `the sidebar says "${badge.textContent}" and the tab "${tab.textContent}"`);
    require(badge.hasAttribute("data-unknown") && !/\d/.test(entry.getAttribute("aria-label")), "the entry is drawn and named as one whose number is not known");
    return window.__TAURI_INTERNALS__.invoke("core_call", { method: "members.list", params: {} }).then(({ team_context: roster }) => {
      require(roster.connected === null && roster.inventory_state !== "complete", "the roster states no number either");
      return { check: name, state: roster.inventory_state };
    });
  }
  if (name === "connector-tools") {
    // An app's page: the account it is signed in with, N of its M tools on with a switch for each, and its actions flush with the dialog's edge.
    const dialog = document.querySelector(".modal-scrim .cs-dialog"), switches = [...(dialog?.querySelectorAll('[data-conn="tool"]') ?? [])];
    require(visible(dialog) && switches.length > 0, "the app's page and the switches of its tools are visible");
    require(dialog.querySelectorAll(".cs-acct").length > 0, "the account it is signed in with is listed");
    const on = switches.filter(node => node.getAttribute("aria-checked") === "true").length, count = dialog.querySelector('[data-k="cs-count"]')?.textContent ?? "";
    require(count.includes(`${on}/${switches.length}`), `the page says "${count}" while ${on} of ${switches.length} tools are on`);
    const acts = dialog.querySelector(".action-row.is-sticky"), box = acts.getBoundingClientRect(), frame = dialog.getBoundingClientRect();
    require(acts.contains(document.elementFromPoint(box.left + box.width / 2, frame.bottom - 3)), "no tool shows through under the actions");
    return { check: name, on, tools: switches.length };
  }
  if (name === "skill-page") {
    const dialog = document.querySelector(".modal-scrim .cs-dialog.wide"), text = dialog?.querySelector("#skill-text");
    require(visible(dialog) && visible(text), "the page of a skill and its document are visible");
    const acts = [...dialog.querySelectorAll(".action-row > *")].flatMap(node => node.matches("button") ? [node] : [...node.querySelectorAll("button")]);
    const del = dialog.querySelector('[data-conn="skill-delete"]'), save = dialog.querySelector('[data-conn="skill-save"]');
    require(acts.indexOf(del) === 0 && acts.at(-1) === save, "delete is first and save is last in the action row");
    require(text.value.startsWith("---") && /\nname: [^\n]+\ndescription: [^\n]+\n---/.test(text.value), "the document is shown whole, from its first line");
    const box = text.getBoundingClientRect(), frame = dialog.getBoundingClientRect();
    require(box.left >= frame.left && box.right <= frame.right, "the document sits inside its dialog");
    require(text.scrollWidth <= text.clientWidth + 1, "the document does not scroll sideways");
    require(text.scrollTop === 0, `the document starts at its first line, not ${text.scrollTop}px down (${text.clientHeight}px box, ${text.scrollHeight}px of text)`);
    return { check: name, rows: text.rows, chars: text.value.length, box: text.clientHeight, text: text.scrollHeight };
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
    plainWords();
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
  if (name === "usage-allowance-link") {
    const heading = document.querySelector(".run-allowance h3");
    require(document.activeElement === heading, "subscription link moves keyboard focus to allowances");
    require(document.querySelectorAll(".run-subscription").length === 3, "all subscription allowances remain available");
    return { check: name, heading: heading.textContent };
  }
  if (name === "usage-bill-breakdown") {
    const bill = document.querySelector(".run-bill-total");
    const sidebar = document.querySelector(".usage-num");
    require(Number(bill.dataset.billTotal) === window.__look.data.status.spend.month_usd, "month bill uses the sidebar ledger total");
    require(sidebar.textContent.split("/")[0].trim() === bill.textContent, "formatted overview bill equals the sidebar amount exactly");
    require(document.querySelectorAll("[data-bill-purpose]").length === 5, "all four purposes and unknown spend are included");
    require(document.querySelector(".run-exclusions").textContent.includes("Fish Audio"), "enabled Fish Audio voice is excluded explicitly");
    require(document.querySelector('[data-run-act="show-allowance"]'), "subscription exclusion links to allowances");
    return { check: name, bill: bill.textContent, sidebar: sidebar.textContent };
  }
  if (name === "usage-summary") {
    require(document.querySelectorAll(".run-tile").length === 5, "overview totals remain available");
    require(!document.querySelector(".run-list, .run-timeline, [data-run-id]"), "per-run navigation is absent");
    const tracks = [...document.querySelectorAll(".run-groups .run-bar-track, .run-allowance .run-bar-track")];
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
  if (name === "bot-ask-centred" || name === "bot-ask-expanded") {
    const event = document.querySelector('[data-handoff="x71"]');
    const [line] = eventLines(1);
    require(line.textContent === "已詢問 小晴已完成", `the asker's line names the colleague and the state: ${line.textContent}`);
    require(visible(line.querySelector('.av')) && !event.closest('.chat-message'), "one faced line outside the messages");
    if (name === "bot-ask-expanded") {
      const detail = event.querySelector('.handoff-detail');
      require(event.open && detail.querySelector('.message-quote')?.textContent.includes("下週上市的新品代號是多少？"), "it opens on the question that was sent");
      require(detail.querySelector('.message-text')?.textContent.includes("NX-2026"), "and on the answer that came back");
      require(!/#\d|x71/.test(detail.textContent), "no internal ids");
    } else require(!event.open, "the line starts closed");
    return { check: name, open: event.open };
  }
  if (name === "bot-message-from" || name === "bot-exchange-count" || name === "bot-exchange-expanded") {
    // The asked member's chat: one centred line where the question came, never a pasted transcript.
    const event = document.querySelector('.exchange-event[data-exchange="71"]');
    const [line] = eventLines(1);
    const words = name === "bot-message-from" ? "訊息來自 小森" : "4 則訊息往來：小森";
    require(line.textContent === words, `the line reads ${words}: ${line.textContent}`);
    require(visible(line.querySelector('.av')), "the line carries the asker's face");
    require(document.querySelector('.tn[data-id="2"]').getBoundingClientRect().bottom <= event.getBoundingClientRect().top, "the question sits where it came, after the member's own earlier turn");
    require(!document.querySelector('.exchange-row .bubble'), "a colleague's question is never an owner bubble");
    if (name === "bot-exchange-expanded") {
      const messages = [...event.querySelectorAll('.exchange-message')].filter(visible);
      require(event.open && messages.length === 4, `every question and answer is shown: ${messages.length}`);
      require(messages.map(message => message.querySelector('b').textContent).join("|") === "小森|小晴|小森|小晴", "each speaker is named once, in order");
      require(!/^(?:小森|小晴)[:：]/m.test(messages.map(message => message.querySelector('.message-text').textContent).join("\n")), "no line carries a speaker label");
    } else require(!event.open, "the line starts closed");
    return { check: name, words: line.textContent };
  }
  if (name === "global-private-memory") {
    const shared = document.querySelector('.mb-det [data-mem-space="shared"]'), own = document.querySelector('.mb-det [data-mem-space="own"]');
    require(visible(shared) && visible(own), "the shared and the member's own memory are both on screen");
    require(shared.textContent.includes("品牌主色是金色") && shared.querySelector('.mem-from')?.textContent === "拉斐爾 · 10/27", "the shared note says whom the owner told it to and when");
    require(own.textContent.includes("專案每週三檢查原型") && !shared.textContent.includes("專案每週三檢查原型"), "the member's own note stays in its own memory");
    return { check: name };
  }
  if (name.startsWith("connector-")) {
    // A connector card (connector-card.ts): the app's own bare mark, its name, where its sign-in stands and at most
    // one framed action, all inside the card, below the reply that asked for it.
    const cards = [...document.querySelectorAll('.connector-card')].filter(visible);
    require(cards.length === 1, `one connector card is visible, found ${cards.length}`);
    const [card] = cards, box = card.getBoundingClientRect(), column = card.closest('.ra-body').getBoundingClientRect();
    require(box.left >= column.left - 1 && box.right <= column.right + 1, "R-UI-29: the card fits the reply column");
    require(box.width >= Math.min(300, column.width) - 1, `the card keeps a card's width, not a chip's (${Math.round(box.width)}px)`);
    const mark = card.querySelector('.mono'), markStyle = getComputedStyle(mark);
    require(visible(mark) && mark.querySelector('svg.logo path'), "R-UI-34: the app's own drawn mark");
    require(markStyle.backgroundColor === 'rgba(0, 0, 0, 0)' && parseFloat(markStyle.borderTopWidth) === 0, "R-UI-34: the mark is bare, on no tile");
    const reply = [...card.closest('.tn').querySelectorAll('.chat-message[data-expandable]')].filter(visible).at(-1);
    require(reply && reply.getBoundingClientRect().bottom <= box.top + 1, "the card sits below the reply that asked for it");
    const buttons = [...card.querySelectorAll('button')].filter(visible);
    for (const button of buttons) {
      const inside = button.getBoundingClientRect();
      require(inside.left >= box.left && inside.right <= box.right && inside.bottom <= box.bottom, "R-UI-25: the action is inside its card");
    }
    const said = card.querySelector('.cc-state').textContent.trim(), app = card.querySelector('.cc-name').textContent;
    const expected = {
      "connector-card": ["pending", "連結後繼續這項工作", "連結", true],
      "connector-authorizing": ["authorizing", "等你在瀏覽器登入…", "取消", false],
      "connector-expired": ["pending", "授權已過期", "重新連結", true],
      "connector-reconnected": ["connected", "已重新連結 · 2 個工具", null, false],
    }[name];
    require(expected, `no expectation for ${name}`);
    const [state, words, action, primary] = expected;
    require(card.dataset.cardState === state && said === words, `${app}'s card reads "${words}" (${card.dataset.cardState}: "${said}")`);
    require(action === null ? buttons.length === 0 : buttons.length === 1 && buttons[0].textContent.trim() === action
      && buttons[0].classList.contains('pri') === primary, `the card's one action is ${action ?? "none"}`);
    if (name === "connector-authorizing") {
      require(visible(card.querySelector('.spin')), "the card shows the sign-in under way");
      require(window.__look.calls.some(call => call.method === "integrations.card_connect" && call.params?.id === "card-gmail"),
        "Connect reached the core with the card's id");
    }
    if (name === "connector-reconnected") {
      const [line] = eventLines(1), resumed = document.querySelector('.tn[data-id="121"]');
      require(line.textContent === "Gmail 已連結，繼續原本的工作", `the resumed task opens with one line naming the app: ${line.textContent}`);
      const markBox = line.querySelector('.mono').getBoundingClientRect();
      require(markBox.width <= 20 && markBox.height <= 20, `the line's mark is no larger than its icons (${markBox.width}x${markBox.height})`);
      require(line.getBoundingClientRect().top >= box.bottom, "the resumed task comes after the card");
      require(!resumed.querySelector('.owner-message'), "the owner's request is not repeated as a new bubble");
      require([...resumed.querySelectorAll('.message-text')].some(text => visible(text) && text.textContent.includes("MC-4F2A")), "the resumed task's answer is shown");
      require(visible(card.querySelector('.cc-done')), "the connected card carries its check");
      plainWords();
    }
    return { check: name, app, state: card.dataset.cardState, words: said };
  }
  if (name.startsWith("secret-") || name.startsWith("pc-")) {
    // The cards of a key (secret-card.ts) and of the owner's turn at the computer (computer-card.ts) are connector cards with a
    // second row: all inside the reply column, below the reply that asked, with real framed buttons inside the card.
    const secret = name.startsWith("secret-"), key = "sk-look-4c9f2b7e81d3";
    const cards = [...document.querySelectorAll(secret ? '.secret-card' : '.computer-card')].filter(visible);
    require(cards.length === 1, `one card is visible, found ${cards.length}`);
    const [card] = cards, box = card.getBoundingClientRect(), column = card.closest('.ra-body').getBoundingClientRect();
    require(box.left >= column.left - 1 && box.right <= column.right + 1, "R-UI-29: the card fits the reply column");
    require(box.width >= Math.min(300, column.width) - 1, `the card keeps a card's width, not a chip's (${Math.round(box.width)}px)`);
    const mark = card.querySelector('.mono'), markStyle = getComputedStyle(mark);
    require(visible(mark) && mark.querySelector('svg'), "the card has its mark");
    require(markStyle.backgroundColor === 'rgba(0, 0, 0, 0)' && parseFloat(markStyle.borderTopWidth) === 0, "R-UI-34: the mark is bare, on no tile");
    if (secret) require(mark.querySelector('svg.logo path'), "R-UI-34: the connection's own drawn mark, not a letter");
    const reply = [...card.closest('.tn').querySelectorAll('.chat-message[data-expandable]')].filter(visible).at(-1);
    require(reply && reply.getBoundingClientRect().bottom <= box.top + 1, "the card sits below the reply that asked for it");
    const buttons = [...card.querySelectorAll('button')].filter(visible);
    for (const button of buttons) {
      const inside = button.getBoundingClientRect();
      require(inside.left >= box.left && inside.right <= box.right && inside.bottom <= box.bottom + 1, "R-UI-25: the action is inside its card");
      require(button.classList.contains('btn') && parseFloat(getComputedStyle(button).borderTopWidth) > 0, "R-UI-17/18: a text action is a framed button");
    }
    const state = card.querySelector('.cc-state').textContent.trim(), calls = window.__look.calls;
    const carrying = call => JSON.stringify(call.params ?? {}).includes(key);
    require(!document.documentElement.outerHTML.includes("開啟電腦") && !document.body.innerText.includes("開啟電腦"), "there is no 開啟電腦 step on the owner's own computer");
    if (name === "secret-masked") {
      const field = card.querySelector('input'), save = buttons[0];
      require(card.dataset.cardState === "pending" && state === "NOTION_TOKEN", `the card names the secret it fills (${state})`);
      require(field.type === "password" && field.autocomplete === "off" && visible(field) && field.getBoundingClientRect().width >= 100, "the field is a masked field a key fits in");
      require(field.value === key && !field.hasAttribute('value'), "the characters live in the field only, never in its markup");
      require(!document.body.innerText.includes(key) && !document.documentElement.outerHTML.includes(key), "no characters of the key are drawn or in the page");
      require(buttons.length === 1 && save.textContent.trim() === "安全儲存" && save.classList.contains('pri') && !save.disabled, "one primary 安全儲存, ready");
      require(visible(card.querySelector('.field svg')), "the field carries its lock");
      require(!calls.some(carrying), "nothing was sent before the owner saved it");
    }
    if (name === "secret-saved-not-resumed") {
      require(card.dataset.cardState === "saved" && card.dataset.cardError === "resume_failed", `saved, and the task did not continue (${card.dataset.cardState}/${card.dataset.cardError})`);
      require(state === "金鑰已儲存，這項工作沒有繼續", `the card says both things: "${state}"`);
      require(!card.querySelector('input') && buttons.length === 1 && buttons[0].textContent.trim() === "再試一次" && buttons[0].classList.contains('pri'), "no field is asked again: one primary 再試一次");
      require(visible(card.querySelector('.cc-name')) && !card.querySelector('.cc-done'), "the check is the resumed task's, not the saved key's");
      const sent = calls.filter(carrying);
      require(sent.length === 1 && sent[0].method === "integrations.card_save", "the key crossed one call, integrations.card_save, and no other");
      require(calls.filter(call => call.method === "agent.ask").length === 0 && !calls.some(call => String(call.method ?? "").startsWith("agent.") && carrying(call)), "never through the assistant");
      require(![...document.querySelectorAll('input, textarea')].some(field => field.value.includes(key)), "no field of the page still holds it");
      require(!document.body.innerText.includes(key) && !document.documentElement.outerHTML.includes(key), "the key is nowhere in the page");
      plainWords();
    }
    if (name.startsWith("pc-")) {
      const live = card.querySelector('.cc-live');
      if (name !== "pc-handback") {
        require(card.dataset.cardState === "control", `the owner has the computer (${card.dataset.cardState})`);
        require(visible(live) && live.textContent.trim() === "你正在控制" && visible(live.querySelector('.dot')), "it says the owner is in control, with a dot and a word");
        require(buttons.map(button => button.textContent.trim()).join("|") === "停止|我完成了" && buttons[1].classList.contains('pri') && !buttons[0].classList.contains('pri'),
          "停止 then the primary 我完成了, the primary last");
        require(state.startsWith("請在「Google Chrome」登入你的帳號，完成後交還控制"), `it names the window to use: "${state}"`);
        require(card.querySelector('.mono svg.i') && card.querySelector('.cc-name span').textContent === "電腦", "the card is the computer's, with its icon");
      }
      if (name === "pc-takeover") {
        require(!card.dataset.cardNote && !card.querySelector('.cc-hint'), "a first hand-over has no hint");
        require(!calls.some(call => String(call.method ?? "").startsWith("integrations.card_")), "nothing was pressed");
      }
      if (name === "pc-human-control") {
        require(card.dataset.cardNote === "still" && card.querySelector('.cc-hint')?.textContent === "看起來還沒完成，請再試一次", "the card says the sign-in does not look done");
        require(calls.filter(call => call.method === "integrations.card_done").length === 1 && !calls.some(call => call.method === "integrations.card_stop"), "我完成了 was pressed once, 停止 never");
        require(!document.querySelector('[data-resumed]'), "the task did not resume");
      }
      if (name === "pc-handback") {
        require(card.dataset.cardState === "done" && state === "已交還控制 · 工作已繼續" && visible(card.querySelector('.cc-done')), `the card says control went back and the task continued: "${state}"`);
        require(buttons.length === 0 && !live, "no action is left, and the owner no longer controls");
        const [line] = eventLines(1), resumed = document.querySelector('.tn[data-id="141"]');
        require(line.textContent === "電腦 已交還控制，繼續原本的工作", `the resumed task opens with one line: ${line.textContent}`);
        const markBox = line.querySelector('svg.i').getBoundingClientRect();
        require(markBox.width <= 20 && markBox.height <= 20, `the line's icon is no larger than the other lines' (${markBox.width}x${markBox.height})`);
        require(line.getBoundingClientRect().top >= box.bottom, "the resumed task comes after the card");
        require(!resumed.querySelector('.owner-message'), "the owner's request is not repeated as a new bubble");
        require([...resumed.querySelectorAll('.message-text')].some(text => visible(text) && text.textContent.includes("YT-4F2A")), "the resumed task's answer is shown");
        require(calls.filter(call => call.method === "integrations.card_done").length === 1, "我完成了 was pressed once");
        plainWords();
      }
    }
    return { check: name, state: card.dataset.cardState, words: state };
  }
  if (name === "local-permission-card" || name === "draft-preference-ask-first") {
    // A permission card (permission-card.ts) is a connector card with a second row for its answers, below the turn that asked.
    // The question it follows is answered there, once: the gold card stays away.
    const waiting = name === "local-permission-card";
    const cards = [...document.querySelectorAll('.permission-card')].filter(visible);
    require(cards.length === 1, `one permission card is visible, found ${cards.length}`);
    const [card] = cards, box = card.getBoundingClientRect(), column = card.closest('.ra-body').getBoundingClientRect();
    require(box.left >= column.left - 1 && box.right <= column.right + 1, "R-UI-29: the card fits the reply column");
    require(box.width >= Math.min(300, column.width) - 1, `the card keeps a card's width, not a chip's (${Math.round(box.width)}px)`);
    const mark = card.querySelector('.mono'), markStyle = getComputedStyle(mark);
    require(visible(mark) && mark.querySelector('svg.logo path'), "R-UI-34: the service's own drawn mark");
    require(markStyle.backgroundColor === 'rgba(0, 0, 0, 0)' && parseFloat(markStyle.borderTopWidth) === 0, "R-UI-34: the mark is bare, on no tile");
    const buttons = [...card.querySelectorAll('button')].filter(visible);
    for (const button of buttons) {
      const inside = button.getBoundingClientRect();
      require(inside.left >= box.left && inside.right <= box.right && inside.bottom <= box.bottom + 1, "R-UI-25: the answer is inside its card");
      require(button.classList.contains('btn') && parseFloat(getComputedStyle(button).borderTopWidth) > 0, "R-UI-17/18: a text answer is a framed button");
      require(!button.disabled, "the answers can be pressed");
    }
    const answers = buttons.map(button => button.textContent.trim()).join("|");
    require(answers === (waiting ? "拒絕|一律允許|允許一次" : "拒絕|允許一次"), `the card answers ${waiting ? "three ways" : "two ways, with no 一律允許 where a rule asks first"}: ${answers}`);
    require(buttons.at(-1).classList.contains('pri') && buttons.slice(0, -1).every(button => !button.classList.contains('pri')), "R-UI-27: the primary answer is the last");
    require(card.querySelector('.cc-name').textContent === "Gmail", "it names the service that asks");
    require(card.dataset.cardState === "pending" && card.querySelector('.cc-ask').textContent.trim() === "是否讓「Gmail」執行「send_email」（to: mei@example.com, subject: 週四下午三點的會議）？",
      "it says the exact action in plain words");
    const gold = document.querySelector('.ask-card');
    require(!gold || !visible(gold), "the question is answered by the card, once: the gold card stays away");
    require(!window.__look.calls.some(call => call.command === "agent_question_answer"), "nothing was answered yet");
    if (waiting) require(!card.querySelector('.cc-hint'), "no rule had it asked: there is no hint");
    else {
      const hint = card.querySelector('.cc-hint');
      require(visible(hint) && hint.textContent.trim() === "此操作符合安全規則「寄信前先問」。", "it says which of the owner's rules had it asked");
      require(![...document.querySelectorAll('.mail-card')].some(visible), "a member that writes no draft first asks the owner: there is no draft card");
    }
    plainWords();
    return { check: name, answers };
  }
  if (name === "member-draft-switch") {
    // The preference to write mail as a draft first is a switch beside the one that asks before contacting colleagues.
    const toggle = document.querySelector('.mb-dialog [data-dlg="draft"]');
    require(visible(toggle) && toggle.getAttribute('role') === 'switch' && toggle.classList.contains('tg'), "the draft preference is a real switch");
    require(toggle.getAttribute('aria-checked') === 'false', "this member was set to send mail itself, so the switch is off");
    const row = toggle.closest('.mp-resource');
    require(row.querySelector('b').textContent === "先擬成草稿，由我寄出", `the switch says what it does: ${row.querySelector('b').textContent}`);
    require(row.querySelector('p').textContent.includes("由你按「傳送」才會寄出"), "its line says that only 傳送 sends");
    require(document.querySelectorAll('.mb-dialog .mp-resource [role="switch"]').length === 2, "it stands beside the one that asks before contacting colleagues");
    return { check: name };
  }
  if (name === "mail-handed-first" || name.startsWith("email-draft-")) {
    // An email card (mail-card.ts): who sends it and to whom, the subject and the body, with 捨棄 and 傳送. Only 傳送 sends,
    // and these scenes never press it: editing, discarding and handing over send nothing.
    const draftCalls = window.__look.calls.filter(call => String(call.method ?? "").startsWith("integrations.draft_"));
    require(window.__look.sent.length === 0 && !draftCalls.some(call => call.method === "integrations.draft_send"), "nothing was sent");
    const first = "a".repeat(64), second = "b".repeat(63) + "2", third = "b".repeat(63) + "3";
    if (name === "mail-handed-first") {
      const [line] = eventLines(1);
      require(line.textContent === "已轉交給 小森", `the first chat keeps one line naming the colleague: ${line.textContent}`);
      require(visible(line.querySelector('.av, svg, .mono')), "the colleague's face stands before the name");
      require(!document.querySelector('.mail-card, .mail-offer'), "the card and its choice are not in this chat any more");
      const handed = draftCalls.filter(call => call.method === "integrations.draft_handover");
      require(handed.length === 1 && handed[0].params.choice === "yes" && handed[0].params.id === "mail-1", "the owner's yes reached the core once");
      require(draftCalls.length === 1, "handing over saved and sent nothing");
      return { check: name, words: line.textContent };
    }
    if (name === "email-draft-discard") {
      const done = [...document.querySelectorAll('.mail-done')].filter(visible);
      require(done.length === 1 && done[0].dataset.mailState === "discarded", "the discarded draft is one quiet line");
      require(done[0].querySelector('.cc-state').textContent.trim() === "已捨棄，沒有寄出任何東西", "it says nothing was sent");
      require(!document.querySelector('.mail-card') && ![...done[0].querySelectorAll('input, textarea, button')].length, "no field or action is left");
      require(draftCalls.length === 1 && draftCalls[0].method === "integrations.draft_discard" && draftCalls[0].params.id === "mail-1", "捨棄 reached the core once and nothing else did");
      const box = done[0].getBoundingClientRect(), column = done[0].closest('.ra-body').getBoundingClientRect();
      require(box.left >= column.left - 1 && box.right <= column.right + 1, "R-UI-29: the line fits the reply column");
      return { check: name };
    }
    const cards = [...document.querySelectorAll('.mail-card')].filter(visible);
    require(cards.length === 1, `one email card is visible, found ${cards.length}`);
    const [card] = cards, box = card.getBoundingClientRect(), column = card.closest('.ra-body').getBoundingClientRect();
    require(box.left >= column.left - 1 && box.right <= column.right + 1, "R-UI-29: the card fits the reply column");
    require(box.width >= Math.min(360, column.width) - 1, `the card keeps a card's width, not a chip's (${Math.round(box.width)}px)`);
    require(card.scrollWidth <= card.clientWidth + 1 && document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, "nothing scrolls sideways");
    const head = card.querySelector('.mail-head .mono');
    require(visible(head) && head.querySelector('svg.logo path'), "R-UI-34: the sending app's own drawn mark");
    require(getComputedStyle(head).backgroundColor === 'rgba(0, 0, 0, 0)' && parseFloat(getComputedStyle(head).borderTopWidth) === 0, "R-UI-34: the mark is bare, on no tile");
    require(!card.querySelector('select'), "no native select: the sender is a dropdown");
    const labels = [...card.querySelectorAll('.mail-k')].filter(visible);
    require(labels.map(label => label.textContent).join("|") === "寄件者|收件者|副本|主旨", `the rows read 寄件者, 收件者, 副本, 主旨: ${labels.map(label => label.textContent)}`);
    const sender = card.querySelector('.mail-row .sel'), inputs = [...card.querySelectorAll('.mail-in')].filter(visible);
    require(visible(sender) && sender.dataset.act === "mail-from" && sender.textContent.trim() === "Gmail", "the sender is a dropdown showing Gmail");
    require(inputs.length === 3 && [sender, ...inputs].every(control => Math.abs(control.getBoundingClientRect().left - sender.getBoundingClientRect().left) <= 1), "the controls of the rows line up");
    require(labels.every(label => Math.abs(label.getBoundingClientRect().left - labels[0].getBoundingClientRect().left) <= 1), "the labels line up");
    const body = card.querySelector('.mail-body');
    require(visible(body) && body.getBoundingClientRect().height >= 100 && body.getBoundingClientRect().right <= box.right, "the body is a field that can be read");
    const buttons = [...card.querySelectorAll('.mail-actions button')].filter(visible);
    for (const button of buttons) {
      const inside = button.getBoundingClientRect();
      require(inside.left >= box.left && inside.right <= box.right && inside.bottom <= box.bottom + 1, "R-UI-25: the action is inside its card");
      require(button.classList.contains('btn') && parseFloat(getComputedStyle(button).borderTopWidth) > 0 && !button.disabled, "R-UI-17/18: a text action is a framed button that can be pressed");
    }
    require(buttons.map(button => button.textContent.trim()).join("|") === "捨棄|傳送" && buttons[1].classList.contains('pri') && !buttons[0].classList.contains('pri'), "R-UI-27: 捨棄 then the primary 傳送");
    require(!card.closest('.tn').querySelector('.event-line, .mail-offer'), "no other line or choice is in this turn");
    if (name === "email-draft-edit") {
      const value = field => card.querySelector(`[data-mail-field="${field}"]`).value;
      require(value("to") === "mei@example.com" && value("cc") === "boss@example.com", "the recipients are what the owner typed");
      require(value("subject") === "回覆：週四下午三點的會議（改期確認）" && value("body").startsWith("小美，您好："), "the subject is what the owner typed and the body is kept");
      require(card.querySelector('.mail-ver').textContent === "第 3 版" && card.dataset.version === "3" && card.dataset.digest === third, "each edit made a new version with a new digest");
      const saves = draftCalls.filter(call => call.method === "integrations.draft_save");
      require(saves.length === 2 && draftCalls.length === 2, "two edits made two saves and nothing else");
      require(saves[0].params.digest === first && saves[1].params.digest === second, "each save is on the digest the version before came back with");
      require(saves[1].params.cc === "boss@example.com" && saves[1].params.subject === "回覆：週四下午三點的會議（改期確認）" && saves[1].params.via === "mcp:gmail/send_email", "a save carries every field as the owner sees it");
      return { check: name, version: card.dataset.version };
    }
    // email-draft-handed-off: the colleague's chat holds the same version, editable, and says whose draft it is.
    require(name === "email-draft-handed-off", `no expectation for ${name}`);
    require(card.dataset.mailState === "draft" && card.dataset.version === "1" && card.dataset.digest === first, "it is the same version, with the same digest");
    require(card.querySelector('.mail-by').textContent === "來自 小晴 的草稿", "it says whose draft it is");
    require(![...card.querySelectorAll('input, textarea')].some(field => field.disabled) && !document.querySelector('.mail-offer, .event-line'), "it is editable, and asks nothing more");
    require(!document.body.innerText.includes("交給你") && !card.closest('.tn').querySelector('.owner-message'), "what the carrier turn is told is not shown, and no bubble is made of it");
    const handed = draftCalls.filter(call => call.method === "integrations.draft_handover");
    require(handed.length === 1 && handed[0].params.choice === "yes" && draftCalls.length === 1, "handing over was answered once, and nothing was saved");
    return { check: name, version: card.dataset.version };
  }
  throw new Error(`Unknown rendered check: ${name}`);
}
