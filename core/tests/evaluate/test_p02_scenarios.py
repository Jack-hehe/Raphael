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
from raphael_core.evaluate.guardian_scenarios import SCENARIOS

from background.conftest import Links
from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient


def test_owner_view_preserves_the_product_primary_contact_term():
    assert not check_reply('主要窗口與幕僚長是拉斐爾。', Scenario('roster', ''))


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
        if self.scenario == 'S07' and 'You are in a group chat' not in user and not any(m.get('role')=='tool' for m in messages):
            calls = [('room_meeting', {'member_ids':[0,1,3,4,5], 'agenda':'Review source evidence',
                                      'roles':{'4':'moderator','1':'pro','5':'con'}})]
            text = ''
        else:
            text, _ = self.answer(system, user)
        return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args)) for n,(name,args) in enumerate(calls)], kwargs['model'], .001, 10, 10, 1)


@pytest.mark.parametrize('scenario', ['S02', 'S07'])
@pytest.mark.parametrize('brain', ['api', 'cli'])
async def test_p02_scenarios_pass_on_both_production_brain_paths(tmp_path, monkeypatch, scenario, brain):
    client = P02Brain(scenario)
    if brain == 'cli':
        class Driver:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, *, system, user, endpoint, token, **kwargs):
                yield Event('model','owner-model')
                if scenario=='S07' and 'You are in a group chat' not in user:
                    async with httpx.AsyncClient() as http:
                        response = await http.post(endpoint, headers={'Authorization':'Bearer ' + token}, json={
                            'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'room_meeting','arguments':{
                                'member_ids':[0,1,3,4,5], 'agenda':'Review source evidence',
                                'roles':{'4':'moderator','1':'pro','5':'con'}}}})
                        assert not response.json()['result']['isError'], response.text
                    answer = '已開啟會議。'
                else:
                    answer, _ = client.answer(system, user)
                yield Event('final',answer)

            async def interrupt(self):
                pass
        monkeypatch.setattr('raphael_core.agent.cli.loop.Driver', Driver)
    driver = RpcDriver(tmp_path, 'fake/model' if brain=='api' else 'cli:codex:owner-model',
                       client, Budget(200), brains=FakeBrains(tmp_path) if brain=='cli' else None)
    links = Links()
    driver.app.background._host = WorkerHost(connect=links)
    try:
        reasons, turns = await driver.execute(next(cell for cell in SCENARIOS if cell.id==scenario))
        assert not reasons, (reasons, [turn.get('text') for turn in turns])
        assert turns
    finally:
        await driver.aclose()
