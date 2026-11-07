"""Server-local administration for an explicit source migration."""
import json
from pathlib import Path

import click

from mycelos.i18n import t


@click.group()
@click.option('--data-dir',type=click.Path(path_type=Path,exists=True),required=True)
@click.option('--user-id',default='default')
@click.pass_context
def cli(ctx,data_dir,user_id):
    ctx.obj={'data_dir':data_dir,'user_id':user_id}


@cli.command('adopt')
@click.option('--device-id',required=True)
@click.option('--grant-file',type=click.Path(path_type=Path,exists=True),required=True)
@click.option('--confirm-local-grant',is_flag=True,required=True)
@click.pass_context
def adopt(ctx,device_id,grant_file,confirm_local_grant):
    from mycelos.app import App
    from .server import CollectorServer
    from .ingest import adopt_source
    try:
        app=App(ctx.obj['data_dir'])
        server=CollectorServer(app)
        if grant_file.stat().st_size>32768:
            raise ValueError()
        grant=json.loads(grant_file.read_text())
        device=server._device(device_id,ctx.obj['user_id'])
        result=adopt_source(app,ctx.obj['user_id'],device,grant,local_grant=confirm_local_grant)
        server._audit('source_adopted',source_id=result['id'],device_id=device_id,user_id=ctx.obj['user_id'])
        click.echo(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError,KeyError,TypeError):
        raise click.ClickException(t('collector.operation_failed')) from None


if __name__=='__main__':
    cli()
