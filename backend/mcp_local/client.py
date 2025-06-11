"""
MCP Client module for connecting to and using MCP servers

This module handles:
1. Connecting to MCP servers via Smithery
2. Converting MCP tools to OpenAPI format for LLMs
3. Executing MCP tool calls

MODIFIED: This version replaces the dependency on the missing 'chuk_mcp'
and 'streamablehttp_client' with a native 'websockets' implementation.
"""

import asyncio
import json
import base64
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from contextlib import asynccontextmanager
import websockets

# Import MCP components according to the official SDK
from mcp import ClientSession
# Import types - these should be in mcp.types according to the docs
try:
    from mcp.types import Tool, CallToolResult as ToolResult
except ImportError:
    # Fallback to a different location if needed
    try:
        from mcp import types
        Tool = types.Tool
        ToolResult = types.CallToolResult
    except AttributeError:
        # If CallToolResult doesn't exist, create a simple class
        Tool = Any
        ToolResult = Any

from utils.logger import logger
import os

# Get Smithery API key from environment
SMITHERY_API_KEY = os.getenv("SMITHERY_API_KEY")
SMITHERY_SERVER_BASE_URL = "https://server.smithery.ai"

@asynccontextmanager
async def websocket_mcp_client(url: str):
    """
    A custom, creative replacement for the missing streamablehttp_client.
    Connects to an MCP server using WebSockets.
    """
    # Transform http(s):// URL to ws(s):// for WebSocket connection
    wss_url = url.replace("https://", "wss://").replace("http://", "ws://")
    logger.info(f"Connecting to WebSocket URL: {wss_url}")
    try:
        async with websockets.connect(wss_url) as websocket:
            # The websocket object can act as both a reader and a writer
            # for the MCP ClientSession.
            yield websocket, websocket, None
    except Exception as e:
        logger.error(f"WebSocket connection to {wss_url} failed: {e}")
        raise

@dataclass
class MCPConnection:
    """Represents a connection to an MCP server"""
    qualified_name: str
    name: str
    config: Dict[str, Any]
    enabled_tools: List[str]
    session: Optional[ClientSession] = None
    tools: Optional[List[Tool]] = None

class MCPManager:
    """Manages connections to multiple MCP servers"""

    def __init__(self):
        self.connections: Dict[str, MCPConnection] = {}
        self._sessions: Dict[str, Tuple[Any, Any, Any]] = {}  # Store streams for cleanup

    async def connect_server(self, mcp_config: Dict[str, Any]) -> MCPConnection:
        """
        Connect to an MCP server using configuration
        """
        qualified_name = mcp_config["qualifiedName"]

        if qualified_name in self.connections:
            logger.info(f"MCP server {qualified_name} already connected")
            return self.connections[qualified_name]

        logger.info(f"Connecting to MCP server: {qualified_name}")

        if not SMITHERY_API_KEY:
            raise ValueError(
                "SMITHERY_API_KEY environment variable is not set. "
                "Please set it to use MCP servers from Smithery."
            )

        try:
            config_json = json.dumps(mcp_config["config"])
            config_b64 = base64.b64encode(config_json.encode()).decode()
            url = f"{SMITHERY_SERVER_BASE_URL}/{qualified_name}/mcp?config={config_b64}&api_key={SMITHERY_API_KEY}"

            # Use our new WebSocket client
            async with websocket_mcp_client(url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    logger.info(f"MCP session initialized for {qualified_name}")
                    tools_result = await session.list_tools()

            tools = tools_result.tools if hasattr(tools_result, 'tools') else tools_result
            logger.info(f"Available tools from {qualified_name}: {[t.name for t in tools]}")

            connection = MCPConnection(
                qualified_name=qualified_name,
                name=mcp_config["name"],
                config=mcp_config["config"],
                enabled_tools=mcp_config.get("enabledTools", []),
                session=None,
                tools=tools
            )

            self.connections[qualified_name] = connection
            return connection

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {qualified_name}: {str(e)}")
            raise

    async def connect_all(self, mcp_configs: List[Dict[str, Any]]) -> None:
        """Connect to all MCP servers in the configuration"""
        for config in mcp_configs:
            try:
                await self.connect_server(config)
            except Exception as e:
                logger.error(f"Failed to connect to {config['qualifiedName']}: {str(e)}")

    def get_all_tools_openapi(self) -> List[Dict[str, Any]]:
        """
        Convert all connected MCP tools to OpenAPI format for LLM
        """
        all_tools = []

        for conn in self.connections.values():
            if not conn.tools:
                continue

            for tool in conn.tools:
                if conn.enabled_tools and tool.name not in conn.enabled_tools:
                    continue

                openapi_tool = {
                    "name": f"mcp_{conn.qualified_name}_{tool.name}",
                    "description": tool.description or f"MCP tool from {conn.name}",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }

                if hasattr(tool, 'inputSchema') and tool.inputSchema:
                    schema = tool.inputSchema
                    if isinstance(schema, dict):
                        openapi_tool["parameters"]["properties"] = schema.get("properties", {})
                        openapi_tool["parameters"]["required"] = schema.get("required", [])

                all_tools.append(openapi_tool)

        return all_tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an MCP tool call
        """
        parts = tool_name.split("_", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            raise ValueError(f"Invalid MCP tool name format: {tool_name}")

        _, qualified_name, original_tool_name = parts

        if qualified_name not in self.connections:
            raise ValueError(f"MCP server {qualified_name} not connected")

        conn = self.connections[qualified_name]
        logger.info(f"Executing MCP tool {original_tool_name} on server {qualified_name}")

        if not SMITHERY_API_KEY:
            raise ValueError("SMITHERY_API_KEY environment variable is not set")

        try:
            config_json = json.dumps(conn.config)
            config_b64 = base64.b64encode(config_json.encode()).decode()
            url = f"{SMITHERY_SERVER_BASE_URL}/{qualified_name}/mcp?config={config_b64}&api_key={SMITHERY_API_KEY}"

            # Use our new WebSocket client for execution as well
            async with websocket_mcp_client(url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(original_tool_name, arguments)

            if hasattr(result, 'content'):
                content = result.content
                if isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if hasattr(item, 'text'):
                            text_parts.append(item.text)
                        elif hasattr(item, 'content'):
                            text_parts.append(str(item.content))
                        else:
                            text_parts.append(str(item))
                    content_str = "\n".join(text_parts)
                else:
                    content_str = str(content) # Simplified if not a list of TextContent-like objects
                is_error = getattr(result, 'isError', False)
            else:
                content_str = str(result)
                is_error = False

            return {
                "content": content_str,
                "isError": is_error
            }

        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {str(e)}")
            return {
                "content": f"Error executing tool: {str(e)}",
                "isError": True
            }

    async def disconnect_all(self):
        """Disconnect all MCP servers (clear stored configurations)"""
        self.connections.clear()
        self._sessions.clear() # Assuming _sessions was for the old client; may not be needed with websockets if not managing streams separately
        logger.info("Cleared all MCP server configurations")

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific tool"""
        parts = tool_name.split("_", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return None

        _, qualified_name, original_tool_name = parts

        conn = self.connections.get(qualified_name)
        if not conn or not conn.tools:
            return None

        for tool in conn.tools:
            if tool.name == original_tool_name:
                return {
                    "server": conn.name,
                    "qualified_name": qualified_name,
                    "original_name": tool.name,
                    "description": tool.description,
                    "enabled": not conn.enabled_tools or tool.name in conn.enabled_tools
                }

        return None
