"""Durable source/recipient contracts, admission slices and visible failure states."""
import asyncio
import json
import sqlite3

import pytest

from raphael_core.agent.history import AgentHistory
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
    assert not any(m['kind']=='wrapup' for m in result['messages'])


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
    assert 'This is round 2' in rig.background.submitted[2]['text']
    assert rig.service.speaker(ids[0]).role == 'PM'


async def test_meeting_reuses_exact_members_and_has_one_agenda(rig, exchange):
    room, ids = await team(rig)
    args = {'member_ids':[0,*ids], 'agenda':'Review evidence'}
    first = await exchange.meeting(args, 'en', 42)
    second = await exchange.meeting(args, 'en', 42)
    assert first['room_id'] == second['room_id'] == room['id']
    assert second['duplicate']
    assert len([m for m in (await history(rig, room))['messages'] if m['member_id'] is None]) == 1


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
