import pytest
from mycelos.tools.settings import execute


def test_settings_tool_only_opens_requested_form():
    context = {}
    assert execute({'section':'models'}, context) == {'status':'form_opened','section':'models'}
    assert context['_pending_events'][0].data == {'widget':{'type':'settings','section':'models'}}


def test_settings_tool_rejects_unregistered_section():
    with pytest.raises(ValueError):
        execute({'section':'arbitrary-html'}, {})
