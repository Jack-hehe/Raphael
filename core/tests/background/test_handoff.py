"""Durable assignments share production gates, worker protocol and CLI adapters offline."""
import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from raphael_core.agent.cli.loop import CliLoop
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.speaker import RAPHAEL, Speaker
from raphael_core.background.tasks import TaskStore
from raphael_core.brain.db import connect
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.settings import SettingsStore
from raphael_core.integrations.client import CallOutcome
from raphael_core.members.store import MemberStore
from raphael_core.memory.service import MemoryService
from raphael_core.worker.loop import Outcome
from .conftest import call, report, turn, until


async def unused(*args,**kwargs):
    pytest.fail('handoff must use the shared background contract')


def team_of(target,*others):
    found = {person.id:person for person in (RAPHAEL,target,*others)}
    return SimpleNamespace(speaker=found.get,roster=lambda:list(found.values()),has_members=lambda:True,
        named_in=lambda text:[target])


def setup(rig, *, model='fake/model', room=True):
    member = MemberStore(rig.conn).create_member(name='Writer',role='Writer')
    target = Speaker(id=member.id,name=member.name,model=model)
    rig.service.use_team(team_of(target),unused)
    history = AgentHistory(rig.conn,rig.wall)
    room_id = thread_id = None
    if room:
        with rig.conn:
            room_id = rig.conn.execute("INSERT INTO rooms(name,created) VALUES ('Work',1)").lastrowid
            rig.conn.executemany('INSERT INTO room_members VALUES (?,?)',[(room_id,0),(room_id,target.id)])
        thread_id = rig.conn.execute('SELECT id FROM room_threads WHERE room_id=?',(room_id,)).fetchone()[0]
    source = history.add(session_id='source',question='Source says 明早截止、明天中午提交。Start now.',
        route='room' if room else 'answer',source='room' if room else 'app',scope='room' if room else 'private',
        room_id=room_id,thread_id=thread_id,prefix='',text='',status='running')
    return target,history.get(source)


def colleague(rig,source,target,name):
    """Another member of the setup room, so nested work need not go back to someone waiting upstream."""
    member = MemberStore(rig.conn).create_member(name=name,role=name)
    other = Speaker(id=member.id,name=member.name,model=target.model)
    rig.service.use_team(team_of(target,other),unused)
    with rig.conn:
        rig.conn.execute('INSERT INTO room_members VALUES (?,?)',(source.room_id,other.id))
    return other


def args(target,**changes):
    return {'member_id':target.id,'request':'Read the source and write the report now. 明天中午提交',
            'stage_id':'draft','done_when':'A verified source receipt and report.',**changes}


@pytest.mark.parametrize('bad,reason', [({'member_id':0},'self_handoff'),({'member_id':9876},'member_unknown_or_removed'),
    ({'ready_at_ms':9999999999999},'bad_arguments'),({'dispatch_policy':'scheduled'},'bad_arguments')])
async def test_refused_handoff_has_named_reason_and_no_receipt(make_rig,bad,reason):
    rig=make_rig()
    target,source=setup(rig)
    result=rig.service.handoff(RAPHAEL,args(target,**bad),source_turn_id=source.id,language='zh-TW')
    assert not result.ok and result.error==reason
    assert result.instruction and reason not in result.instruction
    assert rig.conn.execute('SELECT COUNT(*) FROM task_outbox').fetchone()[0]==0


async def test_a_result_reaches_the_room_after_its_worker_already_replied_there(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    with rig.conn:
        asked=rig.conn.execute("INSERT INTO room_messages(room_id,thread_id,member_id,text,created,turn_id) VALUES (?,?,0,'@Writer check',1,?)",
            (source.room_id,source.thread_id,source.id)).lastrowid
        rig.conn.execute("INSERT INTO room_messages(room_id,thread_id,member_id,text,created,reply_to,reply_to_member) VALUES (?,?,?,'Checked',2,?,0)",
            (source.room_id,source.thread_id,target.id,asked))
    rig.client.replies.append(report('Consulted answer'))
    accepted=rig.service.handoff(RAPHAEL,args(target,mode='consult'),source_turn_id=source.id,language='en')
    task_id=accepted.data['task_id']
    await until(lambda:rig.task(task_id).state in ('done','failed'))
    assert rig.task(task_id).state=='done'
    assert rig.conn.execute("SELECT disposition FROM task_outbox WHERE event_id=?",(f'{task_id}:result',)).fetchone()[0]=='delivered'
    result=rig.conn.execute('SELECT member_id,reply_to,text FROM room_messages WHERE request_id=?',(f'handoff-{task_id}',)).fetchone()
    assert tuple(result)==(target.id,asked,'Consulted answer')


async def test_one_undeliverable_result_leaves_the_others_and_is_delivered_once_fixed(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    rig.client.replies.extend([report('First answer'),report('Second answer'),report('Later task')])
    first=rig.service.handoff(RAPHAEL,args(target,mode='consult',stage_id='one'),source_turn_id=source.id,language='en').data['task_id']
    second=rig.service.handoff(RAPHAEL,args(target,mode='consult',stage_id='two'),source_turn_id=source.id,language='en').data['task_id']
    rig.conn.execute(f"CREATE TRIGGER fail_delivery BEFORE INSERT ON room_messages WHEN NEW.request_id='handoff-{first}' "
                     "BEGIN SELECT RAISE(ABORT,'fixture delivery failure'); END")
    later=rig.submit()
    await until(lambda:rig.task(later).state=='done')
    delivered=lambda task:rig.conn.execute('SELECT delivered_at_ms FROM task_outbox WHERE event_id=?',(f'{task}:result',)).fetchone()[0]
    assert rig.task(first).state==rig.task(second).state=='done'
    assert delivered(first) is None and delivered(second) is not None
    rig.conn.execute('DROP TRIGGER fail_delivery')
    rig.service._deliver_handoffs()
    assert delivered(first) is not None
    assert rig.conn.execute('SELECT text FROM room_messages WHERE request_id=?',(f'handoff-{first}',)).fetchone()[0]=='First answer'


@pytest.mark.parametrize('brain',['api','cli'])
async def test_a_refused_handoff_tells_the_model_what_to_do_instead_of_a_code(make_rig,monkeypatch,caplog,brain):
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model)
    shown=[]
    if brain=='api':
        rig.client.replies.extend([turn(calls=[call('handoff',args(target,member_id=target.id,stage_id='self'))]),report('Answered in place')])
    else:
        rig.service.use_cli(object())
        async def work(loop):
            shown.append((await loop._tool('self','handoff',args(target,member_id=target.id,stage_id='self'))).content)
            loop._queue.put_nowait(Outcome('done',final='Answered in place'))
        monkeypatch.setattr(CliLoop,'_work',work)
    caplog.set_level('INFO')
    accepted=rig.service.handoff(RAPHAEL,args(target,mode='consult'),source_turn_id=source.id,language='en')
    await until(lambda:rig.task(accepted.data['task_id']).state in ('done','failed'))
    if brain=='api':
        shown.append(rig.client.chat_calls[-1]['messages'][-1]['content'])
    assert 'You cannot hand work to yourself; reply in the room or hand it to a colleague.' in shown[0]
    assert 'self_handoff' not in shown[0]
    assert 'self_handoff' in caplog.text


async def test_a_handoff_back_to_a_colleague_waiting_upstream_is_refused(make_rig):
    # Live, members bounced one verification request between them ten times.
    rig=make_rig()
    target,source=setup(rig)
    first=rig.service.handoff(RAPHAEL,args(target,mode='consult'),source_turn_id=source.id,language='en')
    back=rig.service.handoff(target,args(target,member_id=0,stage_id='back'),source_turn_id=first.data['work_turn_id'],language='en')
    assert (back.ok,back.error)==(False,'handoff_cycle') and back.instruction


async def test_a_meeting_turn_gives_the_floor_with_a_mention_instead_of_a_handoff(make_rig):
    # Live, a moderator handed the next speaker a work task and the meeting became a chain of hand-offs.
    rig=make_rig()
    target,source=setup(rig)
    with rig.conn:
        rig.conn.execute('UPDATE rooms SET roles=? WHERE id=?',(json.dumps({str(target.id):'moderator'}),source.room_id))
        origin=rig.conn.execute("INSERT INTO room_messages(room_id,thread_id,text,created) VALUES (?,?,'Debate the launch',1)",
            (source.room_id,source.thread_id)).lastrowid
        rig.conn.execute("INSERT INTO room_exchanges(origin_message_id,room_id,state,language,meeting) VALUES (?,?,'running','en',1)",
            (origin,source.room_id))
        rig.conn.execute("INSERT INTO room_messages(room_id,thread_id,member_id,text,created,turn_id,origin_message_id,kind) "
            "VALUES (?,?,0,'',2,?,?,'message')",(source.room_id,source.thread_id,source.id,origin))
    refused=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='en')
    assert (refused.ok,refused.error)==(False,'meeting_in_progress') and '@mention' in refused.instruction
    # Live, the room kept its moderator after the meeting, so the owner's 「現在執行」 could hand nothing off.
    with rig.conn:
        rig.conn.execute("UPDATE room_exchanges SET meeting=0 WHERE origin_message_id=?",(origin,))
    assert rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='en').ok


async def test_receipt_dedupe_stage_conflict_and_removed_member(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    first=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    again=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    assert first.ok and first.data['task_id']==again.data['task_id'] and again.data['duplicate']
    task=rig.task(first.data['task_id'])
    assert task.state=='queued' and task.ready_at_ms==task.created_at_ms==rig.wall.now
    assert task.scope=='room' and task.model_ref=='fake/model' and task.root_task_id==task.id
    assert json.loads(task.schedule_provenance)['source_turn_id']==source.id
    assert rig.service.handoff(RAPHAEL,args(target,request='Different work'),source_turn_id=source.id,language='zh-TW').error=='stage_conflict'
    with rig.conn:
        rig.conn.execute('DELETE FROM room_members WHERE room_id=? AND member_id=?',(source.room_id,target.id))
    assert rig.service.handoff(RAPHAEL,args(target,stage_id='next'),source_turn_id=source.id,language='zh-TW').error=='member_removed_from_room'
    await until(lambda:rig.task(task.id).state=='failed')
    assert rig.task(task.id).reason=='member_removed_from_room'
    assert rig.service.handoff_views(room_id=source.room_id)[0]['state']=='withheld'


@pytest.mark.parametrize('brain',['api','cli'])
async def test_a_turn_hands_off_every_item_at_once_and_resumes_once(make_rig,monkeypatch,brain):
    # Live, a turn stopped at its first hand-off, so a meeting's items could only start one after another.
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model)
    other=colleague(rig,source,target,'Reviewer')
    items=[args(target),args(other,stage_id='review',request='Review the source now.')]
    parent=rig.service.submit(session_id='parent',text='Start both items now',language='zh-TW',model=model)
    rig.service.attach_turn(parent,source.id)
    if brain=='api':
        rig.client.replies.extend([turn(calls=[*(call('handoff',item,f'h{n}') for n,item in enumerate(items)),call('local_time',{},'t')]),
            report('明早我會完成。'),report('明早我會完成。'),report('兩項都已交辦。')])
    else:
        rig.service.use_cli(object())
        async def work(loop):
            if loop.member_id==0 and 'Delegated work results' not in loop.user:
                for n,item in enumerate(items):
                    assert (await loop._tool(f'h{n}','handoff',item)).ok
                assert (await loop._tool('t','local_time',{})).kind=='waiting_for_handoff'
                assert loop._handed_off()
            elif loop.member_id in (target.id,other.id):
                loop._queue.put_nowait(Outcome('done',final='明早我會完成。'))
            else:
                loop._queue.put_nowait(Outcome('done',final='兩項都已交辦。'))
        monkeypatch.setattr(CliLoop,'_work',work)
    await until(lambda:rig.task(parent).state in ('done','failed'),timeout=10)
    children=rig.service._store.handoffs(source.id)
    assert {child.member_id for child in children}=={target.id,other.id}
    assert all(child.parent_task_id==parent and child.ready_at_ms==child.created_at_ms for child in children)
    assert rig.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(parent,)).fetchone()[0]==2
    if brain=='api':
        unmade=next(m for m in rig.client.chat_calls[-1]['messages'] if m.get('tool_call_id')=='t')
        assert 'waiting for delegated work' in unmade['content']


@pytest.mark.parametrize('brain',['api','cli'])
@pytest.mark.parametrize('deadline',['明早截止','明天中午提交'])
async def test_parent_releases_slot_real_file_result_and_resume_once(make_rig,tmp_path,monkeypatch,brain,deadline):
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model)
    server=await rig.plugins.connect(look='T1')
    output=tmp_path/'result.html'
    nonce='fixture-'+deadline
    receipt=[]
    async def file_tool(server,name,arguments,**kwargs):
        assert rig.task(parent).state=='waiting'
        output.write_text('<!doctype html><p>'+nonce+'</p>',encoding='utf8')
        receipt.append(rig.service._store.verify_artifact(rig.service._run.task_id,'file',output,workspace=tmp_path,mime='text/html'))
        return CallOutcome(json.dumps({'file':output.name,'digest':receipt[-1],'nonce':nonce}),False)
    monkeypatch.setattr(rig.plugins.hub._connector,'call_tool',file_tool)
    parent=rig.service.submit(session_id='parent',text='Delegate a file with '+deadline,language='zh-TW',model=model)
    rig.service.attach_turn(parent,source.id)
    if brain=='api':
        rig.client.replies.extend([turn(calls=[call('handoff',args(target,request='Produce the file. '+deadline))]),
            turn(calls=[call('Notes__look',{'topic':nonce})]),report('已產生驗收檔 '+nonce),report('已核對成果 '+nonce)])
    else:
        rig.service.use_cli(object())
        async def work(loop):
            if loop.member_id==0 and 'Delegated work results' not in loop.user:
                outcome=await loop._tool('handoff','handoff',args(target,request='Produce the file. '+deadline))
                assert outcome.ok and 'task_id' in outcome.content
                unmade=await loop._tool('unmade','local_time',{})
                assert not unmade.ok and unmade.kind=='waiting_for_handoff'
                assert loop._handed_off()
            elif loop.member_id==target.id:
                assert (await loop._tool('file','Notes__look',{'topic':nonce})).ok
                loop._queue.put_nowait(Outcome('done',final='已產生驗收檔 '+nonce))
            else:
                assert nonce in loop.user
                loop._queue.put_nowait(Outcome('done',final='已核對成果 '+nonce))
        monkeypatch.setattr(CliLoop,'_work',work)
    await until(lambda:rig.task(parent).state in ('done','failed'),timeout=10)
    assert rig.task(parent).state=='done', rig.task(parent)
    [child]=rig.service._store.handoffs(source.id)
    assert child.state=='done' and child.parent_task_id==parent and child.root_task_id==parent
    assert child.ready_at_ms==child.created_at_ms and child.dispatch_policy=='immediate'
    assert receipt==[hashlib.sha256(output.read_bytes()).hexdigest()] and nonce in output.read_text(encoding='utf8')
    assert len(rig.service._store.evidence(child.id))==2
    assert rig.conn.execute('SELECT COUNT(*) FROM routines').fetchone()[0]==0
    assert rig.conn.execute("SELECT COUNT(*) FROM room_messages WHERE turn_id=?",(child.turn_id,)).fetchone()[0]==1
    assert rig.conn.execute('SELECT consumed_at_ms FROM task_inbox WHERE parent_task_id=?',(parent,)).fetchone()[0] is not None
    assert rig.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(parent,)).fetchone()[0]==2
    rig.service._deliver_handoffs()
    assert not rig.service._store.resume_from_inbox(parent)
    assert AgentHistory(rig.conn).recent(10,member_id=target.id)==[]
    assert '已核對成果' in AgentHistory(rig.conn).get(source.id).text


class NoMemoryModel:
    async def complete_json(self, **kwargs):
        raise AssertionError('no memory model call is expected')


def knowing(rig, tmp_path, target):
    """The asked member knows a code from its own conversation and a date from its own note; every
    member knows the brand colour."""
    memory = MemoryService(rig.conn, tmp_path/'memory', NoMemoryModel(), MemoryKeyStore())
    memory.store_for(target.id).write('reference-launch', type='reference', description='上市日 PRIVATE-NOTE', body='上市日 PRIVATE-NOTE')
    memory.shared.write('user-brand', type='user', description='品牌主色是金色', body='品牌主色是金色', member=0)
    rig.service.use_memory(memory)
    AgentHistory(rig.conn, rig.wall).add(session_id='private', question='只告訴你：新品代號 PRIVATE-CHAT', route='chat',
                                         prefix='', text='好，我記住了。', status='done', member_id=target.id)


@pytest.mark.parametrize('brain',['api','cli'])
async def test_a_consultation_answers_from_what_the_asked_member_knows_and_the_asker_resumes_once(make_rig,tmp_path,monkeypatch,brain):
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model,room=brain=='cli')
    knowing(rig,tmp_path,target)
    ask=args(target,mode='consult',stage_id='code',request='新品代號是什麼？',done_when='Answer the question.')
    seen={}
    if brain=='api':
        def asked(**kwargs):
            seen['asked']=json.dumps(kwargs['messages'],ensure_ascii=False)
            seen['tools']=[tool['function']['name'] for tool in kwargs['tools']]
            return report('新品代號是 PRIVATE-CHAT。')
        def resumed(**kwargs):
            seen['asker']=json.dumps(kwargs['messages'],ensure_ascii=False)
            return report('行銷說新品代號是 PRIVATE-CHAT。')
        rig.client.replies.extend([turn(calls=[call('handoff',ask)]),asked,resumed])
    else:
        rig.service.use_cli(object())
        async def work(loop):
            if loop.member_id==0 and 'Delegated work results' not in loop.user:
                await loop._tool('handoff','handoff',ask)
            elif loop.member_id==target.id:
                seen['asked']=loop.system+loop.user
                seen['tools']=[tool['function']['name'] for tool in loop.tools]
                loop._queue.put_nowait(Outcome('done',final='新品代號是 PRIVATE-CHAT。'))
            else:
                seen['asker']=loop.user
                loop._queue.put_nowait(Outcome('done',final='行銷說新品代號是 PRIVATE-CHAT。'))
        monkeypatch.setattr(CliLoop,'_work',work)
    parent=rig.service.submit(session_id='parent',text='問行銷新品代號',language='zh-TW',model=model)
    rig.service.attach_turn(parent,source.id)
    await until(lambda:rig.task(parent).state in ('done','failed'),timeout=10)
    assert rig.task(parent).state=='done', rig.task(parent)
    [child]=rig.service._store.handoffs(source.id)
    assert all(known in seen['asked'] for known in ('PRIVATE-CHAT','PRIVATE-NOTE','品牌主色是金色'))
    assert {'memory_read','history_search'} <= set(seen['tools'])
    # Only the answer reaches the asker: never the asked member's conversation or notes.
    assert 'PRIVATE-CHAT' in seen['asker'] and 'PRIVATE-NOTE' not in seen['asker'] and '好，我記住了' not in seen['asker']
    exchange=AgentHistory(rig.conn).get(child.turn_id)
    assert (exchange.question,exchange.text,exchange.member_id,exchange.peer_id)==('新品代號是什麼？','新品代號是 PRIVATE-CHAT。',target.id,0)
    assert rig.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(parent,)).fetchone()[0]==2
    rig.service._deliver_handoffs()
    assert not rig.service._store.resume_from_inbox(parent)
    assert '行銷說新品代號是 PRIVATE-CHAT' in AgentHistory(rig.conn).get(source.id).text


@pytest.mark.parametrize('answer_first',[False,True])
async def test_a_conversation_turn_that_consults_comes_back_once_with_the_answer(make_rig,tmp_path,answer_first):
    rig=make_rig()
    target,source=setup(rig,room=False)
    knowing(rig,tmp_path,target)
    seen={}
    def resumed(**kwargs):
        seen['asker']=json.dumps(kwargs['messages'],ensure_ascii=False)
        return report('行銷說新品代號是 PRIVATE-CHAT。')
    rig.client.replies.extend([report('新品代號是 PRIVATE-CHAT。'),resumed])
    receipt=rig.service.handoff(RAPHAEL,args(target,mode='consult',stage_id='code',request='新品代號是什麼？'),
                                source_turn_id=source.id,language='zh-TW')
    child=rig.task(receipt.data['task_id'])
    asker=rig.task(child.parent_task_id)
    # While the turn is being answered its asker stays off the turn, so the chat shows no second task.
    assert (asker.state,asker.member_id,asker.turn_id)==('running',0,None) and rig.service.tasks_for_turns([source.id])=={}
    if answer_first:
        await until(lambda:rig.task(child.id).state=='done')
        assert rig.task(asker.id).state=='running'
    AgentHistory(rig.conn,rig.wall).resolve(source.id,prefix='答',text='我去問 Writer。',status='done')
    rig.service.settle_asker(source.id,answered=True)
    await until(lambda:rig.task(asker.id).state in ('done','failed'))
    assert rig.task(asker.id).state=='done' and rig.task(asker.id).turn_id==source.id
    assert 'PRIVATE-CHAT' in seen['asker'] and 'PRIVATE-NOTE' not in seen['asker']
    assert '行銷說新品代號是 PRIVATE-CHAT' in AgentHistory(rig.conn).get(source.id).text
    rig.service._deliver_handoffs()
    rig.service.settle_asker(source.id,answered=True)
    assert not rig.service._store.resume_from_inbox(asker.id)
    assert rig.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(asker.id,)).fetchone()[0]==1
    assert rig.client.replies==[]


async def test_a_stopped_conversation_turn_stops_the_questions_it_sent(make_rig,tmp_path):
    rig=make_rig()
    target,source=setup(rig,room=False)
    knowing(rig,tmp_path,target)
    receipt=rig.service.handoff(RAPHAEL,args(target,mode='consult',stage_id='code',request='新品代號是什麼？'),
                                source_turn_id=source.id,language='zh-TW')
    child=rig.task(receipt.data['task_id'])
    AgentHistory(rig.conn,rig.wall).resolve(source.id,prefix='答',text='已取消。',status='denied')
    rig.service.settle_asker(source.id,answered=False)
    assert rig.task(child.id).state=='cancelled' and rig.task(child.parent_task_id).state=='cancelled'
    assert AgentHistory(rig.conn).get(source.id).text=='已取消。'
    assert not rig.reports() and rig.client.chat_calls==[]


@pytest.mark.parametrize('room',[True,False])
async def test_assigned_work_reads_the_shared_notes_but_never_the_assignees_private_ones(make_rig,tmp_path,room):
    # Hand-offs read no memory at all, so a fact the owner gave the whole team never reached the work.
    rig=make_rig()
    target,source=setup(rig,room=room)
    knowing(rig,tmp_path,target)
    rig.client.replies.append(report('明早我會完成。'))
    task_id=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW').data['task_id']
    await until(lambda:rig.task(task_id).state in ('done','failed'))
    [assigned]=rig.client.chat_calls
    shown=json.dumps(assigned['messages'],ensure_ascii=False)
    assert '品牌主色是金色' in shown
    assert 'PRIVATE-CHAT' not in shown and 'PRIVATE-NOTE' not in shown
    tools=[tool['function']['name'] for tool in assigned['tools']]
    assert 'memory_read' in tools and 'history_search' not in tools


@pytest.mark.parametrize('room',[True,False])
async def test_promise_without_action_is_failed_and_receipt_survives_reopen(make_rig,tmp_path,room):
    rig=make_rig()
    target,source=setup(rig,room=room)
    rig.client.replies.append(report('明早我會完成。'))
    result=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    await until(lambda:rig.task(result.data['task_id']).state=='failed')
    task=rig.task(result.data['task_id'])
    assert task.reason=='work_without_action'
    path=tmp_path/'durable.db'
    saved=connect(path)
    rig.conn.backup(saved)
    saved.close()
    reopened=connect(path)
    try:
        assert TaskStore(reopened).get(task.id).idempotency_key==task.idempotency_key
        assert reopened.execute("SELECT disposition FROM task_outbox WHERE type='result'").fetchone()[0]=='delivered'
    finally:
        reopened.close()


@pytest.mark.parametrize('brain',['api','cli'])
async def test_restart_recovers_queued_child_and_waiting_parent_once(make_rig,monkeypatch,brain):
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model)
    team=rig.service._team
    queued=asyncio.Event()
    execute=rig.service._execute
    async def hold_child(task,files=()):
        if task.mode:
            queued.set()
            await asyncio.Event().wait()  # Stop before any child effect is dispatched.
        await execute(task,files)
    monkeypatch.setattr(rig.service,'_execute',hold_child)
    async def cli_work(loop):
        if loop.member_id==0 and 'Delegated work results' not in loop.user:
            await loop._tool('handoff','handoff',args(target))
        elif loop.member_id==target.id:
            assert (await loop._tool('clock','local_time',{})).ok
            loop._queue.put_nowait(Outcome('done',final='已完成重啟後查核。'))
        else:
            assert '已完成重啟後查核' in loop.user
            loop._queue.put_nowait(Outcome('done',final='已核對重啟後成果。'))
    if brain=='cli':
        rig.service.use_cli(object())
        monkeypatch.setattr(CliLoop,'_work',cli_work)
    else:
        rig.client.replies.append(turn(calls=[call('handoff',args(target))]))
    parent=rig.submit(model=model,language='zh-TW')
    rig.service.attach_turn(parent,source.id)
    await asyncio.wait_for(queued.wait(),5)
    [child]=rig.service._store.handoffs(source.id)
    assert rig.task(parent).state=='waiting' and child.state=='queued'
    await rig.service.aclose()
    assert rig.task(parent).state=='waiting' and rig.task(child.id).state=='queued'
    resumed=make_rig()
    assert not resumed.service._handoff_runs
    if brain=='cli':
        resumed.service.use_cli(object())
    else:
        resumed.client.replies.extend([turn(calls=[call('local_time',{})]),report('已完成重啟後查核。'),report('已核對重啟後成果。')])
    async def unused(*args,**kwargs):
        pytest.fail('handoff must use the shared contract')
    resumed.service.use_team(team,unused)
    await until(lambda:resumed.task(parent).state=='done')
    assert resumed.task(child.id).state=='done'
    assert resumed.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(child.id,)).fetchone()[0]==1
    assert resumed.conn.execute("SELECT COUNT(*) FROM room_messages WHERE request_id=?",('handoff-'+str(child.id),)).fetchone()[0]==1
    resumed.service._deliver_handoffs()
    assert not resumed.service._store.resume_from_inbox(parent)


async def test_clearing_handoff_erases_receipts_and_allows_new_work(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    rig.client.replies.append(report('沒有執行。'))
    accepted=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    await until(lambda:rig.task(accepted.data['task_id']).state=='failed')
    rig.service.clear()
    for table in ('worker_tasks','task_executions','task_evidence','task_outbox','task_inbox'):
        assert rig.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    rig.client.replies.append(report('later'))
    later=rig.submit()
    await until(lambda:rig.task(later).state=='done')


async def test_deleted_assignee_keeps_terminal_receipt_and_wakes_parent(make_rig,monkeypatch):
    rig=make_rig()
    target,source=setup(rig)
    pump=rig.service._ensure_pump
    monkeypatch.setattr(rig.service,'_ensure_pump',lambda:None)
    parent=rig.submit(language='zh-TW')
    rig.service._queue.remove(parent)
    rig.service.attach_turn(parent,source.id)
    rig.service._store.start(parent)
    accepted=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    child=accepted.data['task_id']
    rig.service._store.pause_for_children(parent,{'spec':{'task_id':parent,'system':'Report the failure.','user':'Work now.','tools':[],
                                               'limits':{'model_calls':5,'tool_calls':5,'seconds':30}},
                                               'messages':[],'remaining_s':30})
    rig.service.forget_member(target.id)
    assert rig.task(child).state=='failed' and rig.task(child).reason=='member_unknown_or_removed'
    assert rig.task(parent).state=='queued'
    assert rig.service._child_results(parent)[0]['state']=='withheld'
    assert rig.service.handoff_views(room_id=source.room_id)[0]['result']==''
    rig.client.replies.append(report('成員已移除，工作未完成。'))
    monkeypatch.setattr(rig.service,'_ensure_pump',pump)
    pump()
    await until(lambda:rig.task(parent).state=='done')
    assert rig.conn.execute('SELECT COUNT(*) FROM task_executions WHERE task_id=?',(child,)).fetchone()[0]==0
    assert rig.conn.execute('SELECT COUNT(*) FROM room_messages WHERE turn_id=?',(rig.task(child).turn_id,)).fetchone()[0]==0


async def test_deleting_running_assignee_withholds_any_late_result(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    started=asyncio.Event()
    async def hold(**kwargs):
        started.set()
        await asyncio.sleep(30)
        return report('SECRET late result')
    rig.client.replies.extend([turn(calls=[call('local_time',{})]),hold])
    accepted=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    child=accepted.data['task_id']
    await asyncio.wait_for(started.wait(),5)
    rig.service.forget_member(target.id)
    await until(lambda:rig.task(child).state=='failed')
    assert rig.task(child).reason=='member_unknown_or_removed'
    [view]=rig.service.handoff_views(room_id=source.room_id)
    assert view['state']=='withheld' and not view['result'] and not view['evidence']
    assert rig.conn.execute('SELECT COUNT(*) FROM room_messages WHERE turn_id=?',(rig.task(child).turn_id,)).fetchone()[0]==0


async def test_failed_nested_work_cannot_be_reported_as_success(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    checker=colleague(rig,source,target,'Checker')
    rig.client.replies.extend([turn(calls=[call('handoff',args(checker,stage_id='verify'))]),
        report('我明早會查核。'),report('全部完成。')])
    accepted=rig.service.handoff(RAPHAEL,args(target),source_turn_id=source.id,language='zh-TW')
    parent=accepted.data['task_id']
    await until(lambda:rig.task(parent).state=='failed')
    assert rig.task(parent).reason=='child_work_incomplete'
    [child]=rig.conn.execute('SELECT * FROM worker_tasks WHERE parent_task_id=?',(parent,)).fetchall()
    assert child['reason']=='work_without_action'


async def test_required_artifact_and_modified_artifact_cannot_complete(make_rig,tmp_path):
    rig=make_rig()
    target,source=setup(rig)
    task_id,_=rig.service._store.accept_handoff(source=source,parent=None,assignee=target.id,model='fake/model',
        request='Write the report',stage='artifact',mode='work',done_when='A verified HTML file',language='en')
    store=rig.service._store
    store.record_evidence(task_id,'read',kind='tool_receipt',uri='read',digest='receipt',mime='text/plain')
    assert store.completion_reason(store.get(task_id))=='missing_artifact'
    artifact=tmp_path/'report.html'
    artifact.write_text('<p>verified</p>',encoding='utf8')
    store.verify_artifact(task_id,'write',artifact,workspace=tmp_path,mime='text/html')
    assert store.completion_reason(store.get(task_id)) is None
    artifact.write_text('changed',encoding='utf8')
    assert store.completion_reason(store.get(task_id))=='artifact_changed'
    artifact.unlink()
    assert store.completion_reason(store.get(task_id))=='missing_artifact'


async def test_stage_owner_cas_prevents_two_sources_claiming_one_stage(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    store=rig.service._store
    parent=rig.submit()
    rig.service._queue.remove(parent)
    rig.service.attach_turn(parent,source.id)
    store.start(parent)
    first,_=store.accept_handoff(source=source,parent=store.get(parent),assignee=target.id,model='fake/model',
        request='Work',stage='one-owner',mode='work',done_when='Receipt',language='en')
    other=replace(source,id=source.id+100)
    from raphael_core.background.tasks import Refused
    with pytest.raises(Refused,match='stage_conflict'):
        store.accept_handoff(source=other,parent=store.get(parent),assignee=0,model='fake/model',
            request='Duplicate owner',stage='one-owner',mode='work',done_when='Receipt',language='en')
    assert rig.conn.execute('SELECT owner_task_id FROM task_stage_owners').fetchone()[0]==first


@pytest.mark.parametrize('brain',['api','cli'])
async def test_stop_tree_preserves_receipts_and_rejects_late_terminal(make_rig,monkeypatch,brain):
    rig=make_rig()
    model='cli:codex:gpt-5.6-terra' if brain=='cli' else 'fake/model'
    SettingsStore(rig.conn).set('agent_model',model)
    target,source=setup(rig,model=model)
    started=asyncio.Event()
    async def late(**kwargs):
        started.set()
        await asyncio.Event().wait()
    if brain=='api':
        rig.client.replies.extend([turn(calls=[call('handoff',args(target))]),turn(calls=[call('local_time',{})]),late])
    else:
        rig.service.use_cli(object())
        async def work(loop):
            if loop.member_id==0:
                await loop._tool('handoff','handoff',args(target))
            else:
                assert (await loop._tool('clock','local_time',{})).ok
                started.set()
                await asyncio.Event().wait()
        monkeypatch.setattr(CliLoop,'_work',work)
    parent=rig.submit(model=model)
    rig.service.attach_turn(parent,source.id)
    await asyncio.wait_for(started.wait(),5)
    [child]=rig.service._store.handoffs(source.id)
    evidence=rig.service._store.evidence(child.id)
    assert evidence
    run=rig.service._run
    assert (await rig.service._cancel({'task_id':parent}))['cancelled']
    await until(lambda:rig.task(child.id).state=='cancelled')
    assert rig.task(parent).state=='cancelled'
    assert rig.task(parent).generation>0 and rig.task(child.id).generation>0
    rig.service._conclude_run(child,run,{'status':'done','final':'Late result @Writer'})
    assert rig.task(child.id).state=='cancelled'
    assert rig.service._store.evidence(child.id)==evidence
    refused=rig.service.handoff(target,args(target,member_id=0,stage_id='late'),source_turn_id=child.turn_id,language='en')
    assert not refused.ok and refused.error=='cancelled'


async def test_terminal_publication_rolls_back_task_history_and_outbox_together(make_rig,monkeypatch):
    rig=make_rig()
    target,source=setup(rig)
    store=rig.service._store
    task_id,_=store.accept_handoff(source=source,parent=None,assignee=target.id,model='fake/model',
        request='Work',stage='atomic',mode='consult',done_when='Answer',language='en')
    import sqlite3
    def broken():
        rig.conn.execute("UPDATE agent_turns SET text='partial' WHERE id=?",(store.get(task_id).turn_id,))
        raise sqlite3.OperationalError('injected publication failure')
    with pytest.raises(sqlite3.OperationalError):
        store.finish(task_id,'done',prefix='',text='terminal',detail=None,reason=None,publish=broken)
    assert store.get(task_id).state=='queued'
    assert AgentHistory(rig.conn).get(store.get(task_id).turn_id).text==''
    assert not rig.conn.execute("SELECT 1 FROM task_outbox WHERE task_id=? AND type='result'",(task_id,)).fetchone()


async def test_room_progress_is_status_only_and_a_dangling_address_is_never_published(make_rig):
    rig=make_rig()
    target,source=setup(rig)
    async def response(**kwargs):
        task=rig.service._store.get(rig.service._run.task_id)
        for index in range(3):
            rig.service._progress(task,f'Progress chunk {index}')
        return report('Delivered @')
    rig.client.replies.append(response)
    accepted=rig.service.handoff(RAPHAEL,args(target,mode='consult'),source_turn_id=source.id,language='en')
    await until(lambda:rig.task(accepted.data['task_id']).state=='done')
    task=rig.task(accepted.data['task_id'])
    # The reply is kept; only the address that would never be served is dropped.
    assert task.reason is None and AgentHistory(rig.conn).get(task.turn_id).text=='Delivered'
    assert len([event for event in rig.notifications if event[0]=='worker.progress'])==3
    saved=AgentHistory(rig.conn).messages([task.turn_id])[task.turn_id]
    assert all(message['kind']!='progress' and '@' not in message['text'] for message in saved)
    assert all('Delivered @' not in row[0] for row in rig.conn.execute('SELECT text FROM room_messages'))


async def test_failed_terminal_transaction_never_emits_a_success_event(make_rig,monkeypatch):
    rig=make_rig()
    target,source=setup(rig)
    store=rig.service._store
    task_id,_=store.accept_handoff(source=source,parent=None,assignee=target.id,model='fake/model',
        request='Work',stage='publication',mode='consult',done_when='Answer',language='en')
    import sqlite3
    def fail(*args,**kwargs):
        raise sqlite3.OperationalError('publication unavailable')
    monkeypatch.setattr(rig.service._history,'publish_terminal',fail)
    before=len(rig.notifications)
    rig.service._conclude(store.get(task_id),None,'done','','Delivered',None,None)
    assert store.get(task_id).state=='queued'
    assert not rig.notifications[before:]
    assert not rig.conn.execute("SELECT 1 FROM task_outbox WHERE task_id=? AND type='result'",(task_id,)).fetchone()
