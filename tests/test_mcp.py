"""El servidor MCP arranca con el SDK oficial v2 y respeta el modo solo lectura."""
from mcp import Client

import kriptty.mcp_server as server


async def test_mcp_read_only_hides_write_tools():
    async with Client(server.mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"get_market_data", "get_account_state", "get_macro_signals", "get_order_journal"} <= names
    assert "place_order" not in names and "close_position" not in names


async def test_mcp_list_accounts():
    async with Client(server.mcp) as client:
        result = await client.call_tool("list_accounts", {})
    assert not result.is_error
    assert "SUB9" in str(result.structured_content or result.content)
