"""Local collector control. The desktop app calls only these fixed commands."""
from __future__ import annotations

from functools import wraps
import json
from pathlib import Path
import subprocess
import sys

import click

from mycelos.i18n import t
from .client import CollectorClient


def output(value):
    click.echo(json.dumps(value,ensure_ascii=False))


def command(fn):
    @wraps(fn)
    @click.pass_context
    def wrapper(ctx,*args,**kwargs):
        try:
            return fn(ctx,*args,**kwargs)
        except (ValueError,OSError,KeyError,TypeError):
            raise click.ClickException(t('collector.operation_failed')) from None
    return wrapper


def client(ctx):
    return CollectorClient(ctx.obj['directory'],use_keyring=not ctx.obj['file_key'])


def body():
    value=json.loads(sys.stdin.read(16385))
    if not isinstance(value,dict):
        raise ValueError()
    return value


@click.group()
@click.option('--state-dir',type=click.Path(path_type=Path),default=Path.home()/'.mycelos-collector',envvar='MYCELOS_COLLECTOR_STATE')
@click.option('--file-key',is_flag=True,envvar='MYCELOS_COLLECTOR_FILE_KEY',help='Use a private key file when no system key exists.')
@click.pass_context
def cli(ctx,state_dir,file_key):
    ctx.obj={'directory':state_dir,'file_key':file_key}


@cli.command('status')
@command
def status(ctx):
    output(client(ctx).status())


@cli.command('pair')
@command
def pair(ctx):
    output(client(ctx).pair(body()))


@cli.command('add')
@command
def add(ctx):
    value=body()
    if set(value)-{'name','kind','root','workflow','source_id'}:
        raise ValueError()
    output(client(ctx).local.add_source(**value))


@cli.command('discover')
@command
def discover(ctx):
    output(client(ctx).local.discover())


@cli.command('pause')
@command
def pause(ctx):
    item=client(ctx);item.local.pause(True);output(item.status())


@cli.command('resume')
@command
def resume(ctx):
    item=client(ctx);item.local.pause(False);output(item.status())


@cli.command('stop')
@command
def stop(ctx):
    item=client(ctx);item.local.pause(True)
    (item.directory/'stop').touch(mode=0o600)
    output({'status':'stopping'})


@cli.command('run')
@command
def run(ctx):
    client(ctx).run()


def start_daemon(ctx, item):
    if item.daemon_running():
        return
    args=[sys.executable,'-m','mycelos.collector.cli','--state-dir',str(item.directory)]
    if ctx.obj['file_key']:
        args.append('--file-key')
    args.append('run')
    subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


@cli.command('start')
@command
def start(ctx):
    item=client(ctx)
    item.local.pause(False)
    start_daemon(ctx, item)
    output({'status':'starting'})


@cli.command('sync')
@command
def sync(ctx):
    value = body()
    if set(value) != {'source_id'}:
        raise ValueError()
    item = client(ctx)
    item.local.request_sync(value['source_id'])
    if not item.local.status()['paused']:
        start_daemon(ctx, item)
    output({'status': 'queued', 'source_id': value['source_id']})


@cli.command('history')
@command
def history(ctx):
    value = body()
    if set(value) - {'source_id', 'before', 'limit'}:
        raise ValueError()
    output(client(ctx).local.history(**value))


@cli.command('disconnect')
@command
def disconnect(ctx):
    output(client(ctx).disconnect())


@cli.command('open-server')
@command
def open_server(ctx):
    import webbrowser
    from .server import validate_url
    item = client(ctx)
    url = validate_url(item.config['url'])
    if not webbrowser.open(url.rstrip('/')+'/pages/collectors.html', new=2):
        raise ValueError('The server page did not open')
    output({'status': 'opened'})


@cli.command('export-grant')
@command
def export_grant(ctx):
    from uuid import uuid4
    value=body()
    if set(value)!={'source_id'}:
        raise ValueError()
    source=next((s for s in client(ctx).local.list_sources() if s['source_id']==value['source_id']),None)
    if not source:
        raise ValueError()
    output(dict(scan_id=uuid4().hex,source_id=source['source_id'],name=source['name'],kind=source['kind'],
                workflow=source['workflow'],units=[],removed=[],complete=False,rule_version=source.get('rule_version',1)))


@cli.command('rules')
@command
def rules(ctx):
    value=body()
    if set(value)!={'source_id','workflow'}:
        raise ValueError()
    output(client(ctx).local.update_rules(value['source_id'],value['workflow']))


@cli.command('remove')
@command
def remove(ctx):
    value=body()
    if set(value)!={'source_id'}:
        raise ValueError()
    client(ctx).local.remove_source(value['source_id'])
    output({'status':'removed'})


if __name__=='__main__':
    cli()
