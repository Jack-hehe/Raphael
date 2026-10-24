import csv
import io
import json
from datetime import UTC, datetime

import pytest

from raphael_core.brain.runs import RunAnalytics


def at(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


@pytest.fixture
def runs(conn):
    conn.execute("INSERT INTO agent_turns(id,created_at_ms,session_id,question,route,prefix,text,status,cost_usd,model) VALUES(1,?,'s','=SUM(A1)','answer','','ok','done',0.03,'vendor/model')", (at('2026-10-24'),))
    conn.execute("INSERT INTO agent_steps(turn_id,n,at_ms,kind,tool,server,outcome,ms) VALUES(1,1,?,'tool','web_search','Raphael','ok',600)", (at('2026-10-24')+1600,))
    conn.execute("INSERT INTO run_metrics VALUES('turn',1,?,?,120,1,?)", (at('2026-10-24'), at('2026-10-24')+2000, json.dumps([{'at_ms':at('2026-10-24')+1000,'ms':1000,'model':'vendor/model','tokens':120,'outcome':'ok'}])))
    conn.commit()
    return RunAnalytics(conn, lambda: at('2026-10-24T12:00:00'))


async def test_runs_have_real_metrics_and_local_calendar_periods(runs):
    result = await runs.read({'range':'month','tz':'Asia/Taipei'})
    assert result['summary'] == {'duration_ms':2000,'cost_usd':0.03,'tokens':120,'ai_steps':1,'tools':1,'errors':0}
    assert result['runs'][0]['member_name'] == 'Raphael'
    assert result['groups']['model'][0]['label'] == 'vendor/model'
    assert result['start_day'] == '2026-10-01'
    assert (await runs.read({'range':'last_month','tz':'UTC'}))['runs'] == []


async def test_trace_joins_metadata_without_tool_arguments(runs):
    result = await runs.trace({'kind':'turn','id':1})
    assert result['run']['duration_ms'] == 2000
    assert {span['kind'] for span in result['spans']} == {'model','tool'}
    assert result['spans'][0]['ms'] == 1000
    assert 'arguments' not in json.dumps(result)


async def test_csv_exports_every_run_and_prevents_spreadsheet_formulas(runs, conn):
    result = await runs.export({'range':'all','tz':'UTC'})
    rows = list(csv.DictReader(io.StringIO(result['csv'])))
    assert len(rows) == 1
    assert rows[0]['title'] == "'=SUM(A1)"
    assert rows[0]['tokens'] == '120'
    conn.execute('DELETE FROM agent_turns WHERE id=1')
    assert conn.execute('SELECT COUNT(*) FROM run_metrics').fetchone()[0] == 0


async def test_pagination_does_not_limit_totals_or_csv_and_linked_reports_are_not_double_counted(runs, conn):
    conn.execute("INSERT INTO agent_turns(id,created_at_ms,session_id,question,route,prefix,text,status,cost_usd,model) VALUES(2,?,'s','Copied report','routine','','ok','done',0.07,'m')", (at('2026-10-24'),))
    conn.execute("INSERT INTO worker_tasks(id,created_at_ms,state,session_id,turn_id,language,text,cost_usd) VALUES(1,?,'done','s',2,'en','Research task',0.07)", (at('2026-10-24'),))
    page = await runs.read({'range':'all','tz':'UTC','limit':1,'offset':1})
    assert page['total'] == 2 and len(page['runs']) == 1
    assert page['summary']['cost_usd'] == pytest.approx(.1)
    rows = list(csv.DictReader(io.StringIO((await runs.export({'range':'all','tz':'UTC','limit':1}))['csv'])))
    assert {row['title'] for row in rows} == {"'=SUM(A1)", 'Research task'}
    task = await runs.trace({'kind':'task','id':1})
    assert task['run']['duration_ms'] is None and task['run']['tokens'] is None


@pytest.mark.parametrize('params', [{'range':'yesterday'}, {'tz':'not/a-zone'}, {'limit':True}, {'offset':-1}])
async def test_invalid_period_and_page_are_rejected(runs, params):
    from raphael_core.protocol import RpcError
    with pytest.raises(RpcError):
        await runs.read(params)


async def test_calendar_period_starts_at_local_midnight(runs, conn):
    conn.execute("UPDATE agent_turns SET created_at_ms=? WHERE id=1", (at('2026-09-30T18:00:00'),))
    assert (await runs.read({'range':'month','tz':'Asia/Taipei'}))['total'] == 1
    assert (await runs.read({'range':'month','tz':'UTC'}))['total'] == 0
    assert (await runs.read({'range':'week','tz':'UTC'}))['start_day'] == '2026-10-19'
