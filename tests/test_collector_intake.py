import hashlib
import json

import pytest
from mycelos.app import App
from mycelos.collector.server import CollectorServer
from mycelos.collector.crypto import Identity, encode


@pytest.fixture
def env(tmp_path):
    app=App(tmp_path/'server');app.storage.initialize()
    server=CollectorServer(app)
    server.db.execute("INSERT INTO collector_devices(id,user_id,name,public_key,fingerprint,attempt,status,created) VALUES('device','default','Laptop',?,'fingerprint','attempt','active',1)",(encode(Identity.generate().public),))
    return server,server._device('device')


def scan():
    return dict(scan_id='scan1',source_id='source1',name='Documents',kind='folder',rule_version=1,
        workflow=None,units=[dict(external_id='hello.md',title='Hello',content='A private test document.',
        files=['hello.md'],project='',metadata={},fingerprint=hashlib.sha256(b'A private test document.').hexdigest())],removed=[],complete=True)


def test_receive_ack_precedes_processing_and_retry_is_stable(env):
    from mycelos.collector.intake import receive, process_pending
    server,device=env
    body=scan()
    ack=receive(server,device,'scan1',body)
    assert ack['status']=='received'
    assert receive(server,device,'scan1',body)==ack
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready' WHERE id='source1'")
    process_pending(server)
    assert receive(server,device,'scan1',body)['status']=='processed'
    notes=server.db.fetchall("SELECT * FROM knowledge_notes WHERE source IS NOT NULL")
    assert notes
    assert server.db.fetchone('SELECT count(*) AS n FROM collector_receipts')['n']==1


def test_reusing_receipt_id_with_different_content_fails(env):
    from mycelos.collector.intake import receive
    server,device=env
    body=scan();receive(server,device,'scan1',body)
    body['name']='Other'
    with pytest.raises(ValueError):
        receive(server,device,'scan1',body)


def test_expired_temporary_content_is_removed_after_restart(env):
    from mycelos.collector.intake import receive, cleanup, spool_path
    server,device=env
    receive(server,device,'scan1',scan())
    path=spool_path(server,'scan1')
    assert path.exists()
    server.db.execute('UPDATE collector_receipts SET expires=0')
    cleanup(server)
    assert not path.exists()
    assert server.db.fetchone('SELECT status FROM collector_receipts')['status']=='expired'


def test_unapproved_device_cannot_process_queued_data(env):
    from mycelos.collector.intake import receive, process_pending
    server,device=env
    receive(server,device,'scan1',scan())
    server.set_status('default','device','revoked')
    process_pending(server)
    assert server.db.fetchone('SELECT status FROM collector_receipts')['status']=='cancelled'


def test_cross_device_source_and_absolute_provenance_are_rejected(env):
    from mycelos.collector.intake import receive
    server,device=env
    body=scan();body['units'][0]['files']=['/etc/private']
    with pytest.raises(ValueError):
        receive(server,device,'scan1',body)


def test_paused_receipt_survives_resume_without_content_loss(env):
    from mycelos.collector.intake import receive, process_pending, spool_path
    server, device = env
    body = scan()
    receive(server, device, body['scan_id'], body)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    server.set_status('default', device['id'], 'paused')
    process_pending(server)
    assert spool_path(server, body['scan_id']).exists()
    assert not server.db.fetchall('SELECT * FROM knowledge_notes')
    server.set_status('default', device['id'], 'active')
    process_pending(server)
    assert receive(server, device, body['scan_id'], body)['status'] == 'processed'


def test_cancelled_receipt_can_retry_only_after_new_approval(env):
    from mycelos.collector.intake import receive, process_pending
    server, device = env
    body = scan()
    receive(server, device, body['scan_id'], body)
    server.set_status('default', device['id'], 'revoked')
    process_pending(server)
    with pytest.raises(ValueError):
        receive(server, device, body['scan_id'], body)
    # Represent the result of the separate invitation and confirmation flow.
    server.db.execute("UPDATE collector_devices SET status='active'")
    altered = dict(body, name='Changed')
    with pytest.raises(ValueError):
        receive(server, device, body['scan_id'], altered)
    assert receive(server, device, body['scan_id'], body)['status'] == 'received'
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    process_pending(server)
    assert receive(server, device, body['scan_id'], body)['status'] == 'processed'


def test_many_unconfigured_sources_do_not_starve_later_receipts(env):
    from mycelos.collector.intake import receive, process_pending
    server, device = env
    for number in range(26):
        body = dict(scan(), scan_id=f'scan{number}', source_id=f'source{number}')
        receive(server, device, body['scan_id'], body)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready' WHERE id='source25'")
    for _ in range(4):
        process_pending(server)
    assert server.db.fetchone("SELECT status FROM collector_receipts WHERE id='scan25'")['status'] == 'processed'
    assert server.db.fetchone("SELECT count(*) AS n FROM collector_receipts WHERE status='awaiting_configuration'")['n'] == 25
    # A source configured after its turn must also receive another turn.
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready' WHERE id='source0'")
    for _ in range(4):
        process_pending(server)
    assert server.db.fetchone("SELECT status FROM collector_receipts WHERE id='scan0'")['status'] == 'processed'


def test_pending_job_limit_releases_capacity_after_expiry(env):
    import time
    from mycelos.collector.intake import receive, request_job
    server, device = env
    receive(server, device, 'scan1', scan())
    server.db.execute('UPDATE collector_devices SET last_contact=?', (time.time(),))
    for _ in range(50):
        request_job(server, 'default', device['id'], 'scan', {'source_id': 'source1'})
    with pytest.raises(ValueError, match='limit'):
        request_job(server, 'default', device['id'], 'scan', {'source_id': 'source1'})
    server.db.execute('UPDATE collector_jobs SET expires=0')
    assert request_job(server, 'default', device['id'], 'scan', {'source_id': 'source1'})['status'] == 'pending'
