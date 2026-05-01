from app.main import app
import app.mcp_server as mcp_server


def test_natural_language_agent_endpoint_is_not_exposed():
    paths = {route.path for route in app.routes}

    assert '/agent/message' not in paths


def test_mcp_server_exposes_structured_tools_only():
    assert not hasattr(mcp_server, 'agent_message_tool')

    for name in [
        'draft_clear_my_calendar_tool',
        'admin_draft_clear_calendar_tool',
        'admin_list_employees_tool',
        'admin_patch_employee_tool',
        'admin_audit_log_tool',
        'list_pending_actions_tool',
    ]:
        assert hasattr(mcp_server, name)
