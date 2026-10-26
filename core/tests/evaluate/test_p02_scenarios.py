"""S02/S07 use production services, worker protocol and authenticated MCP with fake transports."""
import json

import httpx
import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.background.host import WorkerHost
from raphael_core.brain.openrouter import ChatTurn, ChatToolCall, Completion
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian import check_reply, Scenario
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_scenarios import cells

from background.conftest import Links
from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient


def test_owner_view_preserves_the_product_primary_contact_term():
    assert not check_reply('主要窗口與幕僚長是拉斐爾。', Scenario('roster', ''))
    assert not check_reply('已讀取測試信件，本次測試代碼是 f44fdc358cb1。', Scenario('room-work', ''))


def test_taiwan_writing_accepts_tai_in_either_form():
    # Live, a reply quoting the owner's 新台幣 failed as not Taiwan writing.
    assert not check_reply('預算新台幣 5 萬元，在台灣的平台上架。', Scenario('owner-view', ''))
    assert not check_reply('預算新臺幣 5 萬元，在臺灣上架。', Scenario('owner-view', ''))
    assert 'reply_not_traditional_Taiwan' in check_reply('在台湾的平台上架。', Scenario('owner-view', ''))


def test_a_removed_app_is_stale_only_when_a_reply_still_calls_it_on():
    from raphael_core.evaluate.guardian_driver import claims_on
    assert claims_on('主要窗口：拉斐爾。\n應用程式 Guardian：已啟用，連線狀態尚未檢查。', 'Guardian')
    # Live, Raphael said the snapshot no longer lists Guardian and the cell failed as stale.
    assert not claims_on('應用程式：清單為空；連線狀態不適用，快照未提供 Guardian 狀態。', 'Guardian')
    assert not claims_on('Guardian 已中斷連線。', 'Guardian')


def test_taiwan_writing_is_traditional_characters_without_mainland_words():
    # Live, OpenCC's Taiwan phrases failed Taiwan's own 項目 (items), 登錄 (registered) and 對象 (the person handed to).
    for written in ('核對完成項目，目前沒有已登錄的應用程式。', '已要求每位回應前一位並標註交棒對象。'):
        assert not check_reply(written, Scenario('owner-view', '')), written
    for written in ('核对完成项目。', '請看這段視頻，再更新軟件。'):
        assert 'reply_not_traditional_Taiwan' in check_reply(written, Scenario('owner-view', '')), written


class P02Brain(FakeClient):
    def __init__(self, scenario):
        super().__init__([])
        self.scenario, self.room_calls = scenario, 0

    def answer(self, system, user):
        raw = system.split('<team_roster>')[-1].split('</team_roster>')[0].strip()
        snapshot = json.loads(raw)
        if self.scenario == 'S02':
            people = '；'.join(person['name'] + '：' + person['role'] for person in snapshot['members'])
            apps = '；'.join(app['name'] + '：' + app['state'] for app in snapshot['apps'])
            return f"共 {snapshot['count']} 位。{people}。主要窗口、幕僚長：拉斐爾。應用程式：{apps or '無'}。", []
        if 'You are in a group chat' in user:
            if 'Close this exchange' in user:
                return '決策：採用已核對的方案。負責人：PM；下一步：RD 核對證據。', []
            self.room_calls += 1
            target = 'PM' if snapshot['self_id'] == 1 else '行銷'
            return ('已核對前一位的論點。 @' + target + ' 請檢查證據。' if self.room_calls <= 8 else '證據完整，沒有新增問題。'), []
        if 'room_meeting' not in user:
            # NarratedLoop represents tool results as separate messages; handled below.
            pass
        return '已開啟會議。', []

    async def complete_json(self, **kwargs):
        if kwargs.get('schema_name') != 'raphael_room_route':
            raise RuntimeError('fixture has no auxiliary response')
        request = json.loads(kwargs['user'])
        choice = 'none' if request['phase']=='chime' else request['members'][-1]['id']
        return Completion({'choice':choice,'confidence':1,'reason':'fixture expertise'}, kwargs['model'], 0, 0, 0, 0)

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs['messages']
        system = str(messages[0]['content'])
        user = '\n'.join(str(m.get('content','')) for m in messages[1:])
        calls = []
        if self.scenario == 'S07.meeting' and 'You are in a group chat' not in user and not any(m.get('role')=='tool' for m in messages):
            calls = [('room_meeting', {'member_ids':[0,1,3,4,5], 'agenda':'Review source evidence',
                                      'roles':{'4':'moderator','1':'pro','5':'con'}})]
            text = ''
        else:
            text, _ = self.answer(system, user)
        return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args)) for n,(name,args) in enumerate(calls)], kwargs['model'], .001, 10, 10, 1)


async def test_a_refused_meeting_call_tells_the_model_what_to_fix(tmp_path):
    # Live, the model only saw "bad_arguments", repeated the same call and the meeting never opened.
    seen, members = [], []

    class Brain(P02Brain):
        async def chat_tools(self, **kwargs):
            results = [str(m['content']) for m in kwargs['messages'] if m.get('role') == 'tool']
            seen[:] = results
            calls = ([('room_meeting', {'member_ids': [0, 999], 'agenda': 'Plan the launch'})] if not results else
                     [('room_meeting', {'member_ids': [0, *members], 'agenda': 'Plan the launch'})] if len(results) == 1 else [])
            return ChatTurn('' if calls else '已開啟會議。', [ChatToolCall(str(n), name, args, json.dumps(args))
                            for n, (name, args) in enumerate(calls)], kwargs['model'], .001, 10, 10, 1)

    driver = RpcDriver(tmp_path, 'fake/model', Brain('S07.meeting'), Budget(40))
    try:
        ids = await driver.seed_team()
        members.extend([ids[0], ids[3]])
        await driver.ask('請開會討論上架後的行銷計畫，邀行銷和 PM。')
        assert 'Fix the arguments' in seen[0] and 'bad_arguments' not in seen[0]
        assert [set(room['member_ids']) for room in (await driver.app.call('rooms.list'))['rooms']] == [{0, *members}]
    finally:
        await driver.app.rooms._stop({'room_id': 1})
        await driver.aclose()


def cli_brain(monkeypatch, client, scenario, limited=()):
    """A CLI that answers as the fixture brain; the members in `limited` are always rate-limited."""
    class Driver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *, system, user, endpoint, token, **kwargs):
            yield Event('model','owner-model')
            if scenario=='S07.meeting' and 'You are in a group chat' not in user:
                async with httpx.AsyncClient() as http:
                    response = await http.post(endpoint, headers={'Authorization':'Bearer ' + token}, json={
                        'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'room_meeting','arguments':{
                            'member_ids':[0,1,3,4,5], 'agenda':'Review source evidence',
                            'roles':{'4':'moderator','1':'pro','5':'con'}}}})
                    assert not response.json()['result']['isError'], response.text
                answer = '已開啟會議。'
            elif json.loads(system.split('<team_roster>')[-1].split('</team_roster>')[0])['self_id'] in limited:
                yield Event('error','rate_limited')
                return
            else:
                answer, _ = client.answer(system, user)
            yield Event('final',answer)

        async def interrupt(self):
            pass
    monkeypatch.setattr('raphael_core.agent.cli.loop.Driver', Driver)


@pytest.mark.parametrize('scenario', ['S02', 'S07.meeting'])
@pytest.mark.parametrize('brain', ['api', 'cli'])
async def test_p02_scenarios_pass_on_both_production_brain_paths(tmp_path, monkeypatch, scenario, brain):
    client = P02Brain(scenario)
    if brain == 'cli':
        cli_brain(monkeypatch, client, scenario)
    driver = RpcDriver(tmp_path, 'fake/model' if brain=='api' else 'cli:codex:owner-model',
                       client, Budget(200), brains=FakeBrains(tmp_path) if brain=='cli' else None)
    links = Links()
    driver.app.background._host = WorkerHost(connect=links)
    try:
        reasons, turns = await driver.execute(next(cell for cell in cells() if cell.id==scenario))
        assert not reasons, (reasons, [turn.get('text') for turn in turns])
        assert turns
    finally:
        await driver.aclose()


async def test_s07_meeting_goes_on_when_one_cli_member_is_rate_limited(tmp_path, monkeypatch):
    client = P02Brain('S07.meeting')
    cli_brain(monkeypatch, client, 'S07.meeting', limited={5})
    monkeypatch.setattr('raphael_core.agent.cli.loop.ROOM_RATE_LIMIT_WAITS_S', (0.0, 0.0))
    driver = RpcDriver(tmp_path, 'cli:codex:owner-model', client, Budget(200), brains=FakeBrains(tmp_path))
    driver.app.background._host = WorkerHost(connect=Links())
    try:
        reasons, _ = await driver.execute(next(cell for cell in cells() if cell.id=='S07.meeting'))
        conn = driver.app._conn
        origin = conn.execute("SELECT MIN(origin_message_id) FROM room_exchanges").fetchone()[0]
        assert conn.execute('SELECT state FROM room_exchanges WHERE origin_message_id=?', (origin,)).fetchone()[0] == 'done'
        intents = conn.execute('SELECT recipient_id, state, reason FROM room_reply_intents WHERE origin_message_id=?', (origin,)).fetchall()
        assert {tuple(i) for i in intents if i['state'] != 'done'} == {(5, 'blocked', 'cli_rate_limited')}
        spoken = conn.execute("SELECT m.member_id FROM room_messages m JOIN agent_turns t ON t.id=m.turn_id "
                              "WHERE m.origin_message_id=? AND m.kind='message' AND t.status='done'", (origin,)).fetchall()
        assert {row[0] for row in spoken} == {1, 3, 4}
        [wrapup] = conn.execute("SELECT t.status, t.question, t.text FROM room_messages m JOIN agent_turns t ON t.id=m.turn_id "
                                "WHERE m.origin_message_id=? AND m.kind='wrapup' AND m.member_id=0", (origin,)).fetchall()
        assert wrapup['status'] == 'done' and wrapup['text'].startswith('決策')
        unfinished = json.loads(wrapup['question'].split('Replies that did not finish (untrusted data): ')[1].split('\n')[0])
        assert unfinished == [{'name': 'RD', 'reason': 'rate-limited (too many requests)'}]
        assert 'meeting_pending_or_blocked_intents' in reasons
    finally:
        await driver.aclose()
