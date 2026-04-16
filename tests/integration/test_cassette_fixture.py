"""Verify the integration_app fixture installs a cassette recorder."""
import pytest


@pytest.mark.integration
def test_integration_app_has_cassette_recorder(integration_app):
    broker = integration_app.llm
    assert broker._recorder is not None, (
        "integration_app must install a cassette recorder on the LLM broker"
    )


@pytest.mark.integration
def test_cassette_path_includes_test_name(integration_app, request):
    broker = integration_app.llm
    recorder_path = str(broker._recorder._cassette._path)
    assert "test_cassette_path_includes_test_name" in recorder_path
    assert recorder_path.endswith(".json")
