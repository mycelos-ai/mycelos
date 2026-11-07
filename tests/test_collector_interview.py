from tests.test_collector_intake import env, scan
import pytest


def test_interview_uses_received_samples_without_source_files_or_models(env):
    from mycelos.collector.intake import receive
    from mycelos.collector.interview import preview
    server,device=env
    receive(server,device,'scan1',scan())
    sample=preview(server.app,'default','source1')
    assert sample['samples'][0]['excerpt']=='A private test document.'
    assert sample['analysis_performed'] is False
    assert sample['workflow']['kind']=='folder'
    assert 'root' not in sample
    with pytest.raises(ValueError):
        preview(server.app,'someone-else','source1')


def test_source_tool_exposes_interview_for_collector_source(env):
    from mycelos.collector.intake import receive
    from mycelos.tools.local_sources import execute
    server,device=env;receive(server,device,'scan1',scan())
    result=execute({'action':'interview','source_id':'source1'},{'app':server.app,'user_id':'default'})
    assert result['samples']


def test_interview_does_not_read_expired_temporary_content(env):
    from mycelos.collector.intake import receive
    from mycelos.collector.interview import preview
    server,device=env
    receive(server,device,'scan1',scan())
    server.db.execute('UPDATE collector_receipts SET expires=0')
    assert preview(server.app,'default','source1')['samples']==[]
