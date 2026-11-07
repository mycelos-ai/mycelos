from unittest.mock import MagicMock, PropertyMock, patch
from mycelos.gateway.server import create_app


def test_server_start_does_not_spend_model_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'local-startup-test')
    broker = MagicMock()
    broker.default_model = 'cloud/demo'
    broker._proxy_client = MagicMock()
    with patch('mycelos.app.App.llm', new_callable=PropertyMock, return_value=broker), \
         patch('mycelos.security.proxy_launcher.ProxyLauncher.start', side_effect=RuntimeError('no process in test')), \
         patch('mycelos.gateway.server._start_mcp_connectors'), \
         patch('mycelos.gateway.server._start_telegram_channel'):
        app = create_app(tmp_path, no_scheduler=True)
    assert app.state.mycelos is not None
    broker.complete.assert_not_called()
