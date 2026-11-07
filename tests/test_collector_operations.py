"""Desktop operations use disposable grants, keys, and a local mock transport."""
import fcntl
import json
import threading
import time

import pytest
from click.testing import CliRunner

from mycelos.collector.client import CollectorClient
from mycelos.collector.cli import cli
from mycelos.collector.local import LocalCollector
from tests.test_collector_client import _patch_client_sleep, add_documents, connected



def test_history_waits_for_receiving_ack_without_waiting_for_processing(connected, tmp_path):
    from mycelos.collector.intake import process_pending
    client, server, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    scan = client.local.scan(source['source_id'])
    pending = client.local.history()['entries']
    assert pending[0]['status'] == 'pending'
    assert client.local.list_sources()[0]['last_synced'] is None
    client.cycle(http)
    entries = client.local.history()['entries']
    assert len(entries) == 1
    assert entries[0]['file'] == 'hello.md'
    assert entries[0]['status'] == 'transferred'
    assert entries[0]['id'] > pending[0]['id']
    assert entries[0]['at'] >= pending[0]['at']
    synced = client.local.list_sources()[0]['last_synced']
    assert synced > 0
    process_pending(server)
    assert server.db.fetchone('SELECT status FROM collector_receipts WHERE id=?',
                             (scan['scan_id'],))['status'] == 'awaiting_configuration'
    client.cycle(http)
    assert client.local.history()['entries'] == entries
    assert client.local.list_sources()[0]['last_synced'] == synced
    assert client.local.status()['pending_scans'] == 1
    with client.local._db(readonly=True) as db:
        assert db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 0
    assert str(tmp_path) not in json.dumps(entries)
    assert 'private test document' not in json.dumps(entries)


def test_history_failure_recovery_filter_and_pages(tmp_path):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    first = add_documents(client, tmp_path, 'first')
    second = add_documents(client, tmp_path, 'second')
    scan1 = client.local.scan(first['source_id'])
    client.local.fail_scan(scan1['scan_id'])
    assert client.local.history()['entries'][0]['status'] == 'failed'
    assert client.local.scan(first['source_id']) == scan1
    client.local.acknowledge(scan1['scan_id'])
    client.local.acknowledge(scan1['scan_id'])
    client.local.scan(second['source_id'])
    page = client.local.history(limit=1)
    assert page['entries'][0]['source_id'] == second['source_id']
    assert page['next_before'] is not None
    older = client.local.history(before=page['next_before'], limit=1)
    assert older['entries'][0]['source_id'] == first['source_id']
    assert older['next_before'] is None
    filtered = client.local.history(source_id=first['source_id'])
    assert len(filtered['entries']) == 1
    assert filtered['entries'][0]['status'] == 'transferred'
    client.local.remove_source(first['source_id'])
    assert {'source_id': first['source_id'], 'name': 'first'} in client.local.history()['sources']


def test_status_and_sync_queue_do_not_wait_for_scan_write_lock(tmp_path):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    client.local.pause(True)
    result = []
    with client.local._db():
        def control():
            other = CollectorClient(client.directory, use_keyring=False)
            other.local.request_sync(source['source_id'])
            result.append(other.status())
        thread = threading.Thread(target=control)
        thread.start()
        thread.join(timeout=1)
        completed = not thread.is_alive()
    thread.join(timeout=40)
    assert completed, 'Status and queue must remain available during a scan'
    assert result[0]['paused'] is True
    assert result[0]['sources'][0]['sync_requested'] is True
    assert client.local.claim_sync(source['source_id']) is not None
    assert client.local.list_sources()[0]['sync_requested'] is False


def test_onboarding_needs_active_verified_reply_and_survives_offline(connected):
    client, _, http, _ = connected
    assert client.status()['onboarding_complete'] is False
    client.exchange('poll', {}, http=http)
    assert client.status()['onboarding_complete'] is True
    client.close()
    reloaded = CollectorClient(client.directory, use_keyring=False)
    assert reloaded.status()['onboarding_complete'] is True
    assert reloaded.status()['daemon_running'] is False
    with (client.directory/'daemon.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert reloaded.status()['daemon_running'] is True
    assert reloaded.status()['daemon_running'] is False


def test_disconnect_keeps_originals_and_history_but_removes_grants(connected, tmp_path):
    client, _, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    client.local.scan(source['source_id'])
    client.exchange('poll', {}, http=http)
    client.disconnect()
    reloaded = CollectorClient(client.directory, use_keyring=False)
    state = reloaded.status()
    assert state['onboarding_complete'] is False
    assert state['server_url'] is None
    assert state['sources'] == []
    assert state['pending_scans'] == 0
    assert state['paused'] is True
    assert (tmp_path/'docs'/'hello.md').read_text() == 'A private test document.'
    assert len(reloaded.local.history()['entries']) == 1


def test_disconnect_refuses_to_clear_state_while_daemon_holds_lock(connected, monkeypatch):
    client, _, _, _ = connected
    with (client.directory/'daemon.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError):
            client.disconnect(timeout=0.05)
        assert client.config_path.exists()
        assert (client.directory/'stop').exists()


def test_cli_history_sync_and_disconnect_contract(tmp_path):
    item = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(item, tmp_path, 'docs')
    item.local.pause(True)
    args = ['--state-dir', str(item.directory), '--file-key']
    runner = CliRunner()
    reply = runner.invoke(cli, args+['sync'], input=json.dumps({'source_id': source['source_id']}))
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.output)['status'] == 'queued'
    assert item.local.status()['paused'] is True
    assert item.local.list_sources()[0]['sync_requested'] is True
    reply = runner.invoke(cli, args+['history'], input='{}')
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.output)['entries'] == []
    reply = runner.invoke(cli, args+['disconnect'], input='{}')
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.output)['status'] == 'disconnected'


def test_open_server_uses_only_stored_http_url(connected, monkeypatch):
    client, _, _, _ = connected
    opened = []
    monkeypatch.setattr('webbrowser.open', lambda url, new: opened.append((url, new)) or True)
    args = ['--state-dir', str(client.directory), '--file-key']
    reply = CliRunner().invoke(cli, args+['open-server'])
    assert reply.exit_code == 0, reply.output
    assert opened == [('https://example.test/pages/collectors.html', 2)]
    client.config_path.unlink()
    reply = CliRunner().invoke(cli, args+['open-server'])
    assert reply.exit_code != 0
    assert len(opened) == 1


def test_receiver_processing_error_does_not_downgrade_transferred_file(connected, tmp_path):
    client, server, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    client.cycle(http)
    scan_id = client.local.scan(source['source_id'])['scan_id']
    server.db.execute("UPDATE collector_receipts SET status='error',error=? WHERE id=?",
                      ('SECRET /private/remote/path', scan_id))
    client._request('poll', {}, http)
    entries = client.local.history()['entries']
    assert entries[0]['status'] == 'transferred'
    assert 'SECRET' not in json.dumps(entries)
    assert '/private' not in json.dumps(entries)


def test_history_is_bounded_and_limits_default_and_maximum_page_size(tmp_path):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    # Populate old content-free events directly to avoid ten thousand file reads.
    with client.local._db() as db:
        db.executemany('INSERT INTO transfer_history '
                       '(at,scan_id,source_id,source_name,file,status) VALUES(?,?,?,?,?,?)',
                       [(1, f'old-{n}', source['source_id'], 'docs', f'{n}.md', 'transferred')
                        for n in range(10_000)])
    client.local.scan(source['source_id'])
    assert len(client.local.history()['entries']) == 100
    assert len(client.local.history(limit=900)['entries']) == 500
    with client.local._db(readonly=True) as db:
        assert db.execute('SELECT COUNT(*) FROM transfer_history').fetchone()[0] == 10_000
        assert not db.execute("SELECT 1 FROM transfer_history WHERE scan_id='old-0'").fetchone()


def test_pending_manual_source_runs_before_routine_sources_and_midcycle_request_survives(connected, tmp_path, monkeypatch):
    client, _, http, _ = connected
    first = add_documents(client, tmp_path, 'first')
    second = add_documents(client, tmp_path, 'second')
    client.local.request_sync(second['source_id'])
    calls = []
    original = client.local.scan
    def scan(source_id):
        calls.append(source_id)
        result = original(source_id)
        if source_id == second['source_id']:
            client.local.request_sync(source_id)
        return result
    monkeypatch.setattr(client.local, 'scan', scan)
    client.cycle(http)
    assert calls == [second['source_id'], first['source_id']]
    assert client.local.list_sources()[1]['sync_requested'] is True


def test_ready_profile_cleans_expired_payload_without_waiting_for_busy_scan(tmp_path, monkeypatch):
    from mycelos.collector import local
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    client.local.scan(source['source_id'])
    with client.local._db() as db:
        db.execute('UPDATE outbox SET expires=0')
    reloaded = CollectorClient(client.directory, use_keyring=False)
    assert reloaded.local.history()['entries'][0]['status'] == 'failed'
    with reloaded.local._db(readonly=True) as db:
        assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_offline_request_keeps_retry_delay_instead_of_busy_loop(tmp_path, monkeypatch):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    client.local.request_sync(source['source_id'])
    attempts, sleeps = [], []
    def no_connection(*args):
        raise OSError('Test server unavailable')
    def http_connection(http):
        attempts.append(1)
        if len(attempts) == 3:
            (client.directory/'stop').touch()
        no_connection()
    monkeypatch.setattr(client, 'connect_websocket', no_connection)
    monkeypatch.setattr(client, 'connect_http', http_connection)
    _patch_client_sleep(monkeypatch, sleeps.append)
    client.run()
    assert sum(sleeps) >= 3
    assert client.local.list_sources()[0]['sync_requested'] is True


def test_one_request_wakes_waiting_daemon_within_one_second(connected, tmp_path, monkeypatch):
    client, _, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    cycles, sleeps = [], []
    real_cycle = client.cycle
    def cycle(_http):
        real_cycle(http)
        cycles.append(sum(sleeps))
        if len(cycles) == 2:
            (client.directory/'stop').touch()
    def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 1:
            client.local.request_sync(source['source_id'])
    monkeypatch.setattr(client, 'cycle', cycle)
    _patch_client_sleep(monkeypatch, sleep)
    client.run()
    assert len(cycles) == 2
    assert cycles[1] - cycles[0] <= 1
    assert client.local.list_sources()[0]['sync_requested'] is False


def test_upgrade_keeps_pending_delivery_and_adds_real_history(tmp_path):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    pending = client.local.scan(source['source_id'])
    with client.local._db() as db:
        db.execute('DROP TABLE transfer_history')
        db.execute('ALTER TABLE sources DROP COLUMN last_synced')
    upgraded = CollectorClient(client.directory, use_keyring=False)
    assert upgraded.local.scan(source['source_id']) == pending
    assert upgraded.local.history()['entries'][0]['file'] == 'hello.md'
    assert upgraded.local.history()['entries'][0]['status'] == 'pending'


def test_journal_records_one_entry_per_file_across_multiple_units(tmp_path, monkeypatch):
    import hashlib
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    units = [dict(external_id=f'unit-{n}', title='Unit', content='Test', files=['session.jsonl'],
                  project=None, metadata={}, fingerprint=hashlib.sha256(b'Test').hexdigest()) for n in range(2)]
    monkeypatch.setattr(client.local, '_snapshot_units', lambda source: (units, True))
    client.local.scan(source['source_id'])
    assert len(client.local.history()['entries']) == 1


def test_delayed_receipt_updates_time_and_order_once_with_stable_pages(tmp_path, monkeypatch):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    first = add_documents(client, tmp_path, 'first')
    second = add_documents(client, tmp_path, 'second')
    monkeypatch.setattr('time.time', lambda: 100)
    old = client.local.scan(first['source_id'])
    monkeypatch.setattr('time.time', lambda: 200)
    client.local.scan(second['source_id'])
    monkeypatch.setattr('time.time', lambda: 300)
    client.local.mark_received(old['scan_id'])
    page = client.local.history(limit=1)
    assert page['entries'][0]['source_id'] == first['source_id']
    assert page['entries'][0]['at'] == 300
    assert client.local.history(before=page['next_before'])['entries'][0]['source_id'] == second['source_id']
    monkeypatch.setattr('time.time', lambda: 400)
    client.local.mark_received(old['scan_id'])
    client.local.fail_scan(old['scan_id'])
    client.local.acknowledge(old['scan_id'])
    assert client.local.history(limit=1) == page
    assert client.local.list_sources()[0]['last_synced'] == 300


def test_removed_only_scan_does_not_report_a_file_transfer(tmp_path):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    source = add_documents(client, tmp_path, 'docs')
    scan = client.local.scan(source['source_id'])
    client.local.acknowledge(scan['scan_id'])
    history = client.local.history()['entries']
    (tmp_path/'docs'/'hello.md').unlink()
    removed = client.local.scan(source['source_id'])
    assert removed['removed'] == ['hello.md']
    client.local.acknowledge(removed['scan_id'])
    assert client.local.history()['entries'] == history


def test_original_job_content_has_own_confirmed_file_entry(connected, tmp_path):
    from mycelos.collector.intake import process_pending, request_job
    client, server, http, device = connected
    source = add_documents(client, tmp_path, 'docs')
    scan = client.local.scan(source['source_id'])
    client.cycle(http)
    server.db.execute("UPDATE local_sources SET mode='direct',status='ready'")
    process_pending(server)
    job = request_job(server, 'default', device['id'], 'original', dict(
        source_id=source['source_id'], external_id='hello.md', fingerprint=scan['units'][0]['fingerprint']))
    client.cycle(http)
    entries = client.local.history()['entries']
    assert len(entries) == 2
    assert all(entry['status'] == 'transferred' for entry in entries)
    assert all(entry['file'] == 'hello.md' for entry in entries)
    result = client.local.original(source['source_id'], 'hello.md', scan['units'][0]['fingerprint'])
    client._request('job_result', result, http, id=job['id'])
    assert client.local.history()['entries'] == entries
    assert 'A private test document.' not in json.dumps(entries)


@pytest.mark.parametrize('upgrade', [False, True])
def test_concurrent_profile_open_keeps_schema_changes_atomic(tmp_path, upgrade):
    from concurrent.futures import ThreadPoolExecutor
    errors = []
    for attempt in range(10):
        directory = tmp_path/str(attempt)
        if upgrade:
            existing = LocalCollector(directory)
            with existing._db() as db:
                db.execute('DROP TABLE transfer_history')
                db.execute('ALTER TABLE sources DROP COLUMN last_synced')
                db.execute('ALTER TABLE outbox DROP COLUMN received_at')
        barrier = threading.Barrier(6)
        def open_profile():
            barrier.wait(timeout=5)
            try:
                local = LocalCollector(directory)
                assert local.history()['entries'] == []
            except Exception as error:
                import traceback
                return traceback.format_exc()
        with ThreadPoolExecutor(max_workers=6) as pool:
            errors.extend(error for error in pool.map(lambda _: open_profile(), range(6)) if error)
    assert errors == []


@pytest.mark.parametrize('operation', ['expiry', 'rules', 'remove', 'disconnect'])
def test_local_failure_moves_pending_entry_to_actual_failure_time(tmp_path, monkeypatch, operation):
    client = CollectorClient(tmp_path/'state', use_keyring=False)
    first = add_documents(client, tmp_path, 'first')
    second = add_documents(client, tmp_path, 'second')
    monkeypatch.setattr('time.time', lambda: 100)
    old = client.local.scan(first['source_id'])
    monkeypatch.setattr('time.time', lambda: 200)
    confirmed = client.local.scan(second['source_id'])
    client.local.mark_received(confirmed['scan_id'])
    successful = client.local.history()['entries'][0]
    monkeypatch.setattr('time.time', lambda: 300)
    def fail():
        if operation == 'expiry':
            with client.local._db() as db:
                db.execute('UPDATE outbox SET expires=250 WHERE scan_id=?', (old['scan_id'],))
                client.local._cleanup(db)
        elif operation == 'rules':
            client.local.update_rules(first['source_id'], {'include': ['**/*.txt']})
        elif operation == 'remove':
            client.local.remove_source(first['source_id'])
        else:
            client.disconnect()
    fail()
    entries = client.local.history()['entries']
    assert entries[0]['source_id'] == first['source_id']
    assert entries[0]['status'] == 'failed'
    assert entries[0]['at'] == 300
    assert entries[0]['id'] > successful['id']
    assert entries[1] == successful
    monkeypatch.setattr('time.time', lambda: 400)
    if operation != 'remove':
        fail()
    else:
        client.local.fail_scan(old['scan_id'])
    assert client.local.history()['entries'] == entries
def test_incomplete_scan_reports_failure_despite_successful_delivery(connected, tmp_path, monkeypatch):
    import mycelos.collector.local as module
    client, _, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 1)
    client.cycle(http)
    state = client.status()['sources'][0]
    assert state['status'] == 'incomplete'
    assert state['error'] == 'Scan incomplete'
    assert state['last_synced'] is None
    assert state['sync_requested'] is False
    client.cycle(http)
    assert client.status()['sources'][0]['status'] == 'incomplete'


def test_scan_progress_is_visible_before_reader_and_receiver_finish(connected, tmp_path, monkeypatch):
    client, _, http, _ = connected
    add_documents(client, tmp_path, 'docs')
    scan = client.local.scan
    exchange = client.exchange
    seen = []
    def read(source_id):
        seen.append(client.status()['sources'][0]['status'])
        return scan(source_id)
    def send(message_type, payload, **kwargs):
        if message_type == 'scan':
            seen.append(client.status()['sources'][0]['status'])
        return exchange(message_type, payload, **kwargs)
    monkeypatch.setattr(client.local, 'scan', read)
    monkeypatch.setattr(client, 'exchange', send)
    client.cycle(http)
    assert seen == ['scanning', 'transferring']
    assert client.status()['sources'][0]['last_synced'] is not None
def test_processing_failure_stays_visible_without_automatic_model_retry(connected, tmp_path):
    client, server, http, _ = connected
    source = add_documents(client, tmp_path, 'docs')
    client.cycle(http)
    receipt = server.db.fetchone('SELECT id FROM collector_receipts')
    server.db.execute("UPDATE collector_receipts SET status='error',error='Source processing failed' WHERE id=?", (receipt['id'],))
    client.cycle(http)
    assert server.db.fetchone('SELECT status FROM collector_receipts')['status'] == 'error'
    state = client.status()['sources'][0]
    assert state['status'] == 'processing_failed'
    assert state['last_synced'] is not None
    assert client.local.history()['entries'][0]['status'] == 'transferred'
    # An explicit retry permits the server to process the retained delivery again.
    client.local.request_sync(source['source_id'])
    client.cycle(http)
    assert server.db.fetchone('SELECT status FROM collector_receipts')['status'] == 'received'
