import json
from click.testing import CliRunner
from mycelos.app import App
from mycelos.collector.local import LocalCollector
from mycelos.collector.server import CollectorServer
from mycelos.collector.crypto import Identity,encode
from mycelos.knowledge.local_sources import LocalSourceService


def test_legacy_source_has_supported_explicit_migration_command(tmp_path):
    from mycelos.collector.admin import cli
    from mycelos.collector.cli import cli as local_cli
    app=App(tmp_path/'server');app.storage.initialize();server=CollectorServer(app)
    server.db.execute("INSERT INTO collector_devices(id,user_id,name,public_key,fingerprint,attempt,status,created) VALUES('device','default','Laptop',?,'fp','attempt','active',1)",(encode(Identity.generate().public),))
    root=tmp_path/'notes';root.mkdir();(root/'note.md').write_text('A local source')
    old=LocalSourceService(app).add('Notes','folder',str(root),mode='direct')
    LocalSourceService(app).ingest(old['id'])
    local=LocalCollector(tmp_path/'client');local.add_source('Notes','folder',str(root),source_id=old['id'])
    runner=CliRunner()
    exported=runner.invoke(local_cli,['--state-dir',str(tmp_path/'client'),'--file-key','export-grant'],input=json.dumps({'source_id':old['id']}))
    assert exported.exit_code==0,exported.output
    assert str(root) not in exported.output
    grant=tmp_path/'grant.json';grant.write_text(exported.output)
    result=runner.invoke(cli,['--data-dir',str(app.data_dir),'adopt','--device-id','device','--grant-file',str(grant),'--confirm-local-grant'])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['id']==old['id']
    assert LocalSourceService(app).get(old['id'])['root'].startswith('collector://')
    assert app.storage.fetchone('SELECT count(*) AS n FROM local_source_items')['n']==1
