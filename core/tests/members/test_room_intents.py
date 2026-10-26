"""Durable source/recipient contracts, admission slices and visible failure states."""
import asyncio
import json
import sqlite3

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.members.exchanges import seat
from raphael_core.members.rooms import RoomsService
from .test_exchanges import exchange, team, send, finish, history, Decision  # noqa: F401


@pytest.mark.parametrize('model', ['cli:codex:fixture', 'fake/api'])
async def test_decision_parity_and_explicit_addresses_skip_routing(rig, exchange, model):
    room, ids = await team(rig)
    exchange._settings.set('agent_model', model)
    decision = Decision({'choice':ids[1], 'confidence':.9})
    exchange._exchanges.decision = decision
    await send(rig, room, 'Review the sources')
    assert rig.background.submitted[-1]['member_id'] == ids[1]
    assert len(decision.calls) == 1
    from raphael_core.brain.settings import api_model
    assert decision.calls[0]['model'] == api_model(exchange._settings, model)
    assert not decision.calls[0]['model'].startswith('cli:')
    assert (await history(rig, room))['exchange']['routing']['fallback'] == ''
    await finish(rig, exchange, 'Reviewed')
    await finish(rig, exchange, 'Conclusion')
    decision.calls.clear()
    await send(rig, room, '@Alice inspect', 'two')
    assert not decision.calls


async def test_a_spaced_address_still_hands_the_turn_to_that_colleague(rig, exchange):
    # Models often write 「@ 名字」; dropping the whole reply for it lost a moderator's opening live.
    room, ids = await team(rig)
    await send(rig, room, '@Alice open the review')
    await finish(rig, exchange, 'Opening. @ Bob please start')
    assert rig.background.submitted[-1]['member_id'] == ids[1]
    alice = next(m for m in (await history(rig, room))['messages'] if m['member_id'] == ids[0])
    assert (alice['status'], alice['text']) == ('done', 'Opening. @Bob please start')


async def three(rig):
    room, ids = await team(rig)
    carol = (await rig.call('members.create', {'name':'Carol', 'role':'QA'}))['member']['id']
    room = (await rig.call('rooms.create', {'name':'Review', 'member_ids':[0, *ids, carol]}))['room']
    return room, [*ids, carol]


async def test_two_colleagues_addressing_one_member_get_a_single_reply(rig, exchange):
    # Live, RD answered three times in a row: once per @ and once more for the planned turn.
    room, (alice, bob, carol) = await three(rig)
    await send(rig, room, '@Alice @Bob review the launch plan')
    await finish(rig, exchange, '@Carol please check the budget')
    await finish(rig, exchange, '@Carol please check the timing')
    assert rig.background.submitted[-1]['member_id'] == carol
    await finish(rig, exchange, 'Budget and timing both hold.')
    assert [s['member_id'] for s in rig.background.submitted].count(carol) == 1
    intents = (await history(rig, room))['exchange']['reply_intents']
    assert [i['state'] for i in intents if i['recipient_id'] == carol] == ['done', 'done']


async def test_a_member_who_just_answered_is_not_also_given_the_next_planned_turn(rig, exchange):
    room, (alice, bob, carol) = await three(rig)
    await rig.call('rooms.update', {'room_id':room['id'], 'roles':{str(alice):'moderator'}})
    await send(rig, room, 'Plan the launch')
    await finish(rig, exchange, 'Round one: @Bob what is your view?')
    assert rig.background.submitted[-1]['member_id'] == bob
    await finish(rig, exchange, 'I favour a small test first.')
    assert rig.background.submitted[-1]['member_id'] == carol


async def test_a_trailing_pass_is_not_shown_and_the_wrapup_asks_for_the_owners_headings(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice review', ui_language='zh-TW')
    await finish(rig, exchange, '看起來沒問題。 (pass)')
    alice = next(m for m in (await history(rig, room))['messages'] if m['member_id'] == ids[0])
    assert alice['text'] == '看起來沒問題。'
    for _ in range(3):
        if rig.background.submitted[-1]['member_id'] == 0:
            break
        await finish(rig, exchange, '同意。')
    wrapup = rig.background.submitted[-1]['text']
    assert all(f"'### {heading}'" in wrapup for heading in ('結論', '待你決定', '下一步'))


async def test_timeout_and_invalid_choice_are_observable(rig, exchange, monkeypatch):
    room, ids = await team(rig)
    class Slow:
        async def room_responder(self, **kwargs):
            await asyncio.sleep(10)
    monkeypatch.setattr('raphael_core.members.exchanges.DECISION_TIMEOUT_S', .001)
    exchange._exchanges.decision = Slow()
    await send(rig, room, 'Choose')
    assert (await history(rig, room))['exchange']['routing']['fallback'] == 'timeout'


async def test_source_replay_rename_and_cross_slice_seventh_reply(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice start')
    for n in range(7):
        peer = 'Bob' if n % 2 == 0 else 'Alice'
        previous = rig.background.attached[-1][1]
        await finish(rig, exchange, '@' + peer + ' continue')
        count = len(rig.background.submitted)
        exchange.turn_arrived(previous)
        await exchange.wait_idle()
        assert len(rig.background.submitted) == count
    await rig.call('members.update', {'id':ids[0], 'name':'Anna'})
    await finish(rig, exchange, '@Anna final check')
    assert rig.background.submitted[-1]['member_id'] == ids[0]
    await finish(rig, exchange, 'Ready')
    await finish(rig, exchange, 'Conclusion')
    result = await history(rig, room)
    assert result['exchange']['state'] == 'done'
    intents = result['exchange']['reply_intents']
    assert len({(i['source_message_id'], i['recipient_id']) for i in intents}) == len(intents)
    assert all(i['state'] == 'done' for i in intents)
    assert all(m['reply_to_member'] != m['member_id'] for m in result['messages'] if m['reply_to'] is not None and m['member_id'] is not None)


@pytest.mark.parametrize('action,reason', [('stop','owner'), ('cost','cost'), ('remove','revoked')])
async def test_accepted_intents_become_named_blocks(rig, exchange, action, reason):
    room, ids = await team(rig)
    await send(rig, room, '@Alice and @Bob answer')
    if action == 'stop':
        await rig.call('rooms.stop', {'room_id':room['id']})
    elif action == 'remove':
        await rig.call('rooms.update', {'room_id':room['id'], 'member_ids':[0,ids[0]]})
    await finish(rig, exchange, 'Ready', cost=10 if action=='cost' else .001)
    result = await history(rig, room)
    blocked = [i for i in result['exchange']['reply_intents'] if i['state']=='blocked']
    assert [(i['recipient_id'],i['reason']) for i in blocked] == [(ids[1],reason)]
    assert result['exchange']['state'] == 'stopped'
    summaries=[m for m in result['messages'] if m['kind']=='wrapup']
    assert len(summaries)==1 and summaries[0]['member_id']==0
    assert result['messages'][-1]['kind']=='wrapup'
    named = {'owner':[], 'cost':['Bob：預算已用完'], 'revoked':['Bob：已不在房間']}[reason]
    first = '已停止本次工作，已完成的動作保留。' if reason=='owner' else '本次討論沒有全部完成。'
    assert summaries[0]['text'].split('\n')[1:2+len(named)] == [first, *named]
    await exchange._exchanges.drive(room['id'])
    assert len([m for m in (await history(rig,room))['messages'] if m['kind']=='wrapup'])==1


LIMITED = 'Codex CLI 目前請求太頻繁而被限流，請稍後再試。'


async def fail(rig, exchange, reason='cli_rate_limited'):
    task_id, turn = rig.background.attached[-1]
    AgentHistory(rig.conn).resolve(turn, prefix='', text=LIMITED, status='error')
    rig.background.store.finish(task_id, 'failed', prefix='', text=LIMITED, detail=None, reason=reason)
    exchange.turn_arrived(turn)
    await exchange.wait_idle()


def wrapups(result):
    return [m['text'] for m in result['messages'] if m['kind'] == 'wrapup']


async def test_a_failed_reply_blocks_only_its_intent_and_raphael_still_wraps_up(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice and @Bob answer')
    await fail(rig, exchange)
    assert [s['member_id'] for s in rig.background.submitted] == ids
    await finish(rig, exchange, 'Ready')
    assert rig.background.submitted[-1]['member_id'] == 0
    prompt = rig.background.submitted[-1]['text']
    assert 'Replies that did not finish' in prompt and '"name": "Alice"' in prompt and 'rate-limited' in prompt
    await finish(rig, exchange, 'Conclusion: Alice was rate-limited; Bob is ready.')
    result = await history(rig, room)
    assert result['exchange']['state'] == 'done'
    assert [(i['recipient_id'], i['state'], i['reason']) for i in result['exchange']['reply_intents']] == [
        (ids[0], 'blocked', 'cli_rate_limited'), (ids[1], 'done', '')]
    assert wrapups(result) == ['Conclusion: Alice was rate-limited; Bob is ready.']


async def test_a_failed_meeting_turn_is_named_and_owner_stop_still_stops_everyone(rig, exchange):
    room, ids = await team(rig)
    await rig.call('rooms.update', {'room_id':room['id'], 'roles':{str(ids[0]):'moderator', str(ids[1]):'con'}})
    await send(rig, room, 'Discuss the evidence')
    await fail(rig, exchange)
    assert [s['member_id'] for s in rig.background.submitted] == ids
    await rig.call('rooms.stop', {'room_id':room['id']})
    await finish(rig, exchange, 'Evidence reviewed')
    result = await history(rig, room)
    assert len(rig.background.submitted) == 2
    assert (result['exchange']['state'], result['exchange']['reason']) == ('stopped', 'owner')
    assert [(i['recipient_id'], i['reason']) for i in result['exchange']['reply_intents']] == [(ids[0], 'cli_rate_limited')]
    [text] = wrapups(result)
    assert text.split('\n')[1:3] == ['已停止本次工作，已完成的動作保留。', 'Alice：請求太頻繁而被限流']


async def test_a_reply_cut_off_by_the_owners_stop_is_not_called_an_error(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice and @Bob answer')
    await rig.call('rooms.stop', {'room_id':room['id']})
    task_id, turn = rig.background.attached[-1]
    AgentHistory(rig.conn).resolve(turn, prefix='', text='Stopped.', status='denied')
    rig.background.store.finish(task_id, 'cancelled', prefix='', text='Stopped.', detail=None, reason='cancelled')
    exchange.turn_arrived(turn)
    await exchange.wait_idle()
    result = await history(rig, room)
    assert [(i['recipient_id'], i['reason']) for i in result['exchange']['reply_intents']] == [(ids[0], 'owner'), (ids[1], 'owner')]
    [text] = wrapups(result)
    assert text.split('\n')[1:3] == ['已停止本次工作，已完成的動作保留。', '沒有已驗證的交付成果。']


async def test_the_no_model_wrapup_names_a_rate_limit_without_claiming_an_owner_stop(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice and @Bob answer')
    await fail(rig, exchange)
    await finish(rig, exchange, 'Ready')
    await fail(rig, exchange)
    result = await history(rig, room)
    assert (result['exchange']['state'], result['exchange']['reason']) == ('stopped', 'cli_rate_limited')
    failed, text = wrapups(result)
    assert failed == LIMITED
    assert text.split('\n')[1:3] == ['本次討論沒有全部完成。', 'Alice：請求太頻繁而被限流']
    assert '已停止本次工作' not in text


async def test_restart_settles_completed_source_once_and_preserves_queued_intents(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice start')
    turn = rig.background.attached[-1][1]
    AgentHistory(rig.conn).resolve(turn, prefix='', text='@Bob check', status='done')
    await exchange.aclose()
    restored = RoomsService(rig.conn, members=rig.service, background=rig.background, decision=Decision())
    restored.start()
    await restored.wait_idle()
    assert [s['member_id'] for s in rig.background.submitted] == ids
    await restored._exchanges.drive(room['id'])
    assert len(rig.background.submitted) == 2
    await restored.aclose()


async def test_blocked_wrapup_publication_is_atomic_and_retriable(rig, exchange):
    room, ids = await team(rig)
    await send(rig,room,'@Alice inspect')
    rig.conn.execute("CREATE TRIGGER fail_wrapup BEFORE INSERT ON room_messages WHEN NEW.kind='wrapup' BEGIN SELECT RAISE(ABORT,'fixture publication failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='fixture publication failure'):
        await finish(rig,exchange,'No result',cost=10)
    assert not rig.conn.execute("SELECT 1 FROM agent_turns WHERE session_id LIKE 'room-stop:%'").fetchone()
    rig.conn.execute('DROP TRIGGER fail_wrapup')
    await exchange._exchanges.drive(room['id'])
    result=await history(rig,room)
    assert result['messages'][-1]['member_id']==0 and result['messages'][-1]['kind']=='wrapup'
    assert 'No verified deliverables' in result['messages'][-1]['text'] or '沒有已驗證的交付成果' in result['messages'][-1]['text']


async def test_served_mentions_remain_visible_after_the_recipient_is_renamed(rig,exchange):
    room,ids=await team(rig)
    await send(rig,room,'@Alice begin')
    await finish(rig,exchange,'@Bob inspect')
    await finish(rig,exchange,'Delivered')
    await finish(rig,exchange,'Conclusion')
    await rig.call('members.update',{'id':ids[1],'name':'Robert'})
    result=await history(rig,room)
    assert any(m['text']=='@Bob inspect' and m['status']=='done' for m in result['messages'])


async def test_source_settlement_is_atomic_with_its_reply_intent(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice start')
    active = rig.background.attached[-1][1]
    rig.conn.execute("CREATE TRIGGER fail_settlement BEFORE UPDATE OF state ON room_reply_intents "
                     "WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'fixture interruption'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture interruption'):
        await finish(rig, exchange, '@Bob check')
    assert exchange._exchanges.get(1).active_turn == active
    assert rig.conn.execute('SELECT exchange_settled FROM room_messages WHERE turn_id=?', (active,)).fetchone()[0] == 0
    rig.conn.execute('DROP TRIGGER fail_settlement')
    await exchange._exchanges.drive(room['id'])
    assert [s['member_id'] for s in rig.background.submitted] == ids
    assert rig.conn.execute("SELECT COUNT(*) FROM room_reply_intents WHERE state='done'").fetchone()[0] == 1


async def test_restart_uncertain_work_is_named_and_never_replayed(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice and @Bob start')
    await exchange.aclose()
    restored = RoomsService(rig.conn, members=rig.service, background=rig.background)
    restored.start()
    await restored.wait_idle()
    result = await restored._messages({'room_id':room['id']})
    assert all(i['state']=='blocked' and i['reason']=='restart' for i in result['exchange']['reply_intents'])
    assert len(rig.background.submitted) == 1
    await restored.aclose()


@pytest.mark.parametrize('foreign_keys', [False, True])
async def test_a_missing_worker_turn_blocks_its_accepted_intent(rig, exchange, foreign_keys):
    room, ids = await team(rig)
    await send(rig, room, '@Alice start')
    active = rig.background.attached[-1][1]
    await exchange.aclose()
    rig.conn.execute(f'PRAGMA foreign_keys={int(foreign_keys)}')
    with rig.conn:
        rig.conn.execute('DELETE FROM agent_turns WHERE id=?', (active,))
    intent = rig.conn.execute('SELECT * FROM room_reply_intents').fetchone()
    assert intent['turn_id'] == (None if foreign_keys else active)
    restored = RoomsService(rig.conn, members=rig.service, background=rig.background)
    restored.start()
    await restored.wait_idle()
    result = await restored._messages({'room_id':room['id']})
    assert result['exchange']['reply_intents'][0]['state'] == 'blocked'
    assert len(rig.background.submitted) == 1
    await restored.aclose()


async def test_room_roles_run_two_rounds_without_changing_personas(rig, exchange):
    room, ids = await team(rig)
    await rig.call('rooms.update', {'room_id':room['id'], 'roles':{str(ids[0]):'moderator',str(ids[1]):'con'}})
    await send(rig, room, 'Discuss the evidence')
    while (await history(rig, room))['exchange']['state'] != 'done':
        await finish(rig, exchange, 'Evidence reviewed')
    assert [s['member_id'] for s in rig.background.submitted] == [*ids,*ids,0]
    assert 'Open round 2' in rig.background.submitted[2]['text']
    assert rig.service.speaker(ids[0]).role == 'PM'


async def test_a_meeting_runs_two_rounds_then_the_moderator_closes_and_raphael_concludes_once(rig, exchange):
    # Live, every reply handed the floor on with an @, the moderator kept asking, and two rounds ran past forty turns.
    room, (alice, bob, carol) = await three(rig)
    await rig.call('rooms.update', {'room_id':room['id'], 'roles':{str(alice):'moderator', str(bob):'pro', str(carol):'con'}})
    await send(rig, room, '@Alice @Bob @Carol debate the launch')
    prompts = []
    for reply in ('Should we launch now? @Bob', 'Launch now. @Carol', 'Test first. @Alice', 'Speed against cost: @Carol',
                  'Speed costs more. @Bob', 'The cost is small. @Alice', 'Both sides are heard. @Raphael please conclude'):
        prompts.append(rig.background.submitted[-1]['text'])
        await finish(rig, exchange, reply)
    await finish(rig, exchange, '### Conclusion\nTest first.\n### Owner decision\nNone.\n### Next steps\nBob: run the test.')
    result = await history(rig, room)
    assert [(m['member_id'], m['kind']) for m in result['messages'] if m['member_id'] is not None] == [
        (alice, 'message'), (bob, 'message'), (carol, 'message'), (alice, 'message'),
        (carol, 'message'), (bob, 'message'), (alice, 'message'), (0, 'wrapup')]
    assert result['exchange']['state'] == 'done'
    assert all(i['state'] == 'done' for i in result['exchange']['reply_intents'])
    assert 'Open round 1' in prompts[0] and 'Still to speak in round 1: @Carol' in prompts[1]
    assert 'hand back to the moderator @Alice' in prompts[2] and 'Open round 2' in prompts[3]
    assert 'Still to speak in round 2: @Bob' in prompts[4] and 'hand back to the moderator @Alice' in prompts[5]
    assert 'Both rounds are complete' in prompts[6] and '@拉斐爾' in prompts[6]


async def test_after_a_meeting_the_owners_next_message_is_not_another_debate(rig, exchange):
    # 「開始做吧」 after a meeting started a fresh two-round debate, during which no one could hand off work.
    room, ids = await team(rig)
    await rig.call('rooms.update', {'room_id':room['id'], 'roles':{str(ids[0]):'moderator', str(ids[1]):'con'}})
    await send(rig, room, 'Discuss the evidence')
    while (await history(rig, room))['exchange']['state'] != 'done':
        await finish(rig, exchange, 'Evidence reviewed')
    asked = len(rig.background.submitted)
    await send(rig, room, '@Bob start on the plan now', 'two')
    assert [s['member_id'] for s in rig.background.submitted[asked:]] == [ids[1]]
    assert 'Open round' not in rig.background.submitted[-1]['text']
    await finish(rig, exchange, 'Started.')
    assert (await history(rig, room))['exchange']['pending'] == '[]'
    while (await history(rig, room))['exchange']['state'] != 'done':
        await finish(rig, exchange, 'Noted.')
    meeting = await exchange.meeting({'member_ids':[0,*ids], 'agenda':'Discuss the next release'}, 'en', 45)
    assert meeting['room_id'] == room['id'] and 'Open round 1' in rig.background.submitted[-1]['text']


@pytest.mark.parametrize('plan,member,phase', [
    ([{'member':1, 'round':2}, {'member':2, 'round':2}], 2, 'aside'),
    ([{'member':2, 'round':1}, {'member':1, 'round':2}, {'member':2, 'round':2}], 1, 'floor'),
    ([{'member':3, 'round':1}, {'member':1, 'round':2}, {'member':2, 'round':2}, {'member':3, 'round':2}], 2, 'aside'),
    ([], 2, 'after')])
def test_a_turn_outside_its_planned_place_keeps_the_plan(plan, member, phase):
    # Between rounds, mid-round or after them, an addressed member answers without using up a planned turn.
    rest, meeting = seat(plan, member, 1)
    assert (meeting['phase'], rest) == (phase, plan)


async def test_meeting_reuses_exact_members_and_has_one_agenda(rig, exchange):
    room, ids = await team(rig)
    args = {'member_ids':[0,*ids], 'agenda':'Review evidence'}
    first = await exchange.meeting(args, 'en', 42)
    second = await exchange.meeting(args, 'en', 42)
    assert first['room_id'] == second['room_id'] == room['id']
    assert second['duplicate']
    assert len([m for m in (await history(rig, room))['messages'] if m['member_id'] is None]) == 1


async def test_meeting_roles_accept_names_and_a_supporting_part(rig, exchange):
    # Live, a model keyed roles by name or gave a colleague a part outside the enum, and the meeting never opened.
    room, ids = await team(rig)
    result = await exchange.meeting({'member_ids':[0,*ids], 'agenda':'Plan the launch',
                                     'roles':{'Alice':'moderator', 'Bob':'social content'}}, 'en', 43)
    assert result['room_id'] == room['id']
    roles = (await rig.call('rooms.list'))['rooms'][0]['roles']
    assert roles == {str(ids[0]):'moderator', str(ids[1]):'participant'}


@pytest.mark.parametrize('parts,expected', [
    ({'Alice':'主持人', 'Bob':'正方', 'Carol':'反方'}, ['moderator', 'pro', 'con']),
    ({'Alice':'Chair', 'Bob':'for', 'Carol':'against'}, ['moderator', 'pro', 'con']),
    ({'Alice':'社群內容補充', 'Bob':'content for the launch', 'Carol':'pro side'}, ['participant', 'participant', 'pro'])])
async def test_meeting_roles_understand_the_owners_words(rig, exchange, parts, expected):
    # Live, every part the model named fell outside the four words, so the meeting lost its moderator.
    room, ids = await three(rig)
    await exchange.meeting({'member_ids':[0,*ids], 'agenda':'Plan the launch', 'roles':parts}, 'zh-TW', 44)
    roles = next(r for r in (await rig.call('rooms.list'))['rooms'] if r['id'] == room['id'])['roles']
    assert [roles[str(i)] for i in ids] == expected


async def test_snapshot_schema_is_current_and_secret_free(rig):
    first = rig.service.team_context(0, 'en')
    assert first['count'] == len(first['members']) == 1 and first['self_id'] == 0
    assert first['primary_id'] == first['chief_id'] == 0
    member = (await rig.call('members.create', {'name':'Alice', 'role':'Researcher'}))['member']
    second = rig.service.team_context(member['id'], 'en')
    assert second['count'] == 2 and second['revision'] != first['revision']
    rig.service._connections = lambda: [{'id':'one','name':'Mail','enabled':True,'status':'error','error':'auth','token':'never-public'}]
    third = rig.service.team_context(member['id'], 'en')
    assert third['apps'][0]['state'] == 'needs_sign_in'
    assert 'never-public' not in json.dumps(third)
    await rig.call('members.update', {'id':member['id'], 'name':'Renamed'})
    assert rig.service.team_context(0, 'en')['revision'] != third['revision']
    rig.service._connections = lambda: []
    assert not rig.service.team_context(0, 'en')['apps']


async def test_quotes_code_and_plain_names_do_not_create_intents(rig, exchange):
    room, ids = await team(rig)
    assert exchange._targets(room, 'Alice; `@Bob`; ```\n@Alice\n```\n> @Bob\n"@Alice" jo@Bob.com') == []


@pytest.mark.parametrize('action', ['stop','interjection'])
async def test_late_routing_cannot_submit_a_stale_generation(rig, exchange, action):
    room, ids = await team(rig)
    entered, release = asyncio.Event(), asyncio.Event()
    class HeldDecision:
        async def room_responder(self, **kwargs):
            entered.set()
            await release.wait()
            return {'choice':ids[0], 'confidence':1}
    exchange._exchanges.decision = HeldDecision()
    old = asyncio.create_task(send(rig, room, 'Choose a lead'))
    await entered.wait()
    new = asyncio.create_task(rig.call('rooms.stop', {'room_id':room['id']}) if action=='stop' else send(rig, room, '@Bob latest question', 'two'))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(old, new)
    assert [s['member_id'] for s in rig.background.submitted] == ([] if action=='stop' else [ids[1]])


async def test_reply_target_and_other_explicit_mentions_are_both_accepted(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, '@Alice check')
    await finish(rig, exchange, 'Ready')
    await finish(rig, exchange, 'Conclusion')
    source = next(m for m in (await history(rig, room))['messages'] if m['member_id']==ids[0])
    await send(rig, room, '@Bob also check', 'two', reply_to=source['id'])
    assert [i['recipient_id'] for i in (await history(rig, room))['exchange']['reply_intents']] == ids
