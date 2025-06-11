from typing import List, Dict, Any, Optional

from agentpress.tool import Tool
from utils.logger import logger
from models.mcp import McpConfig
from mcp_local.client import MCPManager

# Global MCP Manager instance
mcp_manager: Optional[MCPManager] = None

async def get_mcp_manager(force_reload: bool = False, mcp_configs: Optional[List[McpConfig]] = None) -> MCPManager:
    """
    Get the global MCP Manager instance, creating it if it doesn't exist.
    Caches the manager to avoid reconnecting to servers on every call.
    """
    global mcp_manager
    if mcp_manager is None or force_reload:
        if force_reload and mcp_manager:
            await mcp_manager.disconnect_all()

        mcp_manager = MCPManager()
        if mcp_configs:
            # Convert Pydantic models to dicts
            dict_configs = [config.dict() for config in mcp_configs]
            await mcp_manager.connect_all(dict_configs)

    return mcp_manager

class MCPToolWrapper(Tool):
    """
    A wrapper for executing MCP tools.
    """
    def __init__(self, mcp_configs: List[McpConfig]):
        super().__init__(
            name="mcp_tool_executor",
            description="Executes a tool from a connected MCP server.",
            input_schema={
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "The full name of the tool to execute (e.g., mcp_exa_web_search_exa)."
                    },
                    "arguments": {
                        "type": "object",
                        "description": "The arguments for the tool."
                    }
                },
                "required": ["tool_name", "arguments"]
            }
        )
        # Store configs for potential reloads
        self.mcp_configs = mcp_configs

    async def run(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the specified MCP tool with the given arguments.
        """
        tool_name = args.get("tool_name")
        arguments = args.get("arguments", {})

        if not tool_name:
            return {"error": "tool_name is a required argument."}

        logger.info(f"Executing MCP tool via wrapper: {tool_name}")

        try:
            # Get the cached MCP manager
            manager = await get_mcp_manager(mcp_configs=self.mcp_configs)

            # Execute the tool
            result = await manager.execute_tool(tool_name, arguments)

            return result

        except ValueError as e:
            logger.error(f"MCP tool execution value error: {str(e)}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"An unexpected error occurred during MCP tool execution: {str(e)}")
            return {"error": f"An unexpected error occurred: {str(e)}"}
