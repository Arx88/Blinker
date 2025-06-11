
from typing import Optional, TYPE_CHECKING

from agentpress.thread_manager import ThreadManager
from agentpress.tool import Tool
# from daytona_sdk import Sandbox # No longer directly used as type hint
from .sandbox import get_sandbox # Updated import for the factory
from .abs_sandbox import AbstractSandbox # Updated import for type hint
from utils.logger import logger
from utils.files_utils import clean_path

if TYPE_CHECKING:
    # This is to avoid circular imports if AbstractSandbox needs to import something from a module
    # that might indirectly import SandboxToolsBase. For type hinting only.
    pass

class SandboxToolsBase(Tool):
    """Base class for all sandbox tools that provides project-based sandbox access."""
    
    # Class variable to track if sandbox URLs have been printed
    _urls_printed = False
    
    def __init__(self, project_id: str, thread_manager: Optional[ThreadManager] = None):
        super().__init__()
        self.project_id = project_id
        self.thread_manager = thread_manager
        self.workspace_path = "/workspace" # Default workspace path within the sandbox
        self._sandbox: Optional[AbstractSandbox] = None # Updated type hint
        self._sandbox_id: Optional[str] = None # This will be the Daytona workspace ID or Docker container name
        self._sandbox_pass: Optional[str] = None # VNC password, if applicable

    async def _ensure_sandbox(self) -> AbstractSandbox: # Updated return type hint
        """Ensure we have a valid sandbox instance, retrieving it from the project if needed."""
        if self._sandbox is None:
            if self.thread_manager is None or self.thread_manager.db is None:
                # This case can happen if a tool is instantiated outside a managed thread (e.g. tests)
                # We might need a direct way to get a sandbox without DB lookup, or this tool won't work.
                # For now, if no thread_manager.db, try to get a sandbox using project_id as sandbox_id/label.
                logger.warning("ThreadManager or DB client not available in _ensure_sandbox. Attempting to get sandbox directly.")
                if not self.project_id: # project_id is essential if no DB to look up sandbox_id
                    raise ValueError("Project ID is required to get a sandbox, especially without DB access.")
                # Use project_id as the sandbox_id for Docker or project_id_label for Daytona
                # This assumes project_id can serve as a unique identifier for the sandbox instance.
                self._sandbox_id = self.project_id # Or a derivative if needed
                logger.info(f"Attempting to get sandbox with ID/label '{self._sandbox_id}' directly (no DB lookup).")
                # auto_create=True because if we are here, we likely need a sandbox to operate.
                # Project_id is used as the label for Daytona to find or create.
                # For Docker, this sandbox_id will be used as container_name.
                self._sandbox = get_sandbox(sandbox_id=self._sandbox_id, auto_create=True, project_id_label=self.project_id)

            else: # Standard path: Get sandbox_id from project data in DB
                try:
                    # Get database client
                    client = await self.thread_manager.db.client

                    # Get project data
                    project = await client.table('projects').select('*').eq('project_id', self.project_id).execute()
                    if not project.data or len(project.data) == 0:
                        raise ValueError(f"Project {self.project_id} not found in DB")

                    project_data = project.data[0]
                    sandbox_info = project_data.get('sandbox', {})

                    # 'id' in sandbox_info is the Daytona workspace ID or similar unique ID for other sandbox types
                    db_sandbox_id = sandbox_info.get('id')
                    if not db_sandbox_id:
                        logger.warning(f"No sandbox ID found in DB for project {self.project_id}. Will attempt creation using project_id as label.")
                        # If no specific sandbox ID is stored, we'll use the project_id as a label/name
                        # and let get_sandbox handle creation or lookup based on that.
                        self._sandbox_id = self.project_id # Fallback to project_id as identifier
                    else:
                        self._sandbox_id = db_sandbox_id

                    self._sandbox_pass = sandbox_info.get('pass') # VNC password

                    logger.info(f"Getting sandbox for project {self.project_id} with sandbox ID/label '{self._sandbox_id}'. auto_create=True.")
                    # Get or start the sandbox using the factory
                    # auto_create=True: if sandbox_id from DB doesn't exist (e.g. deleted from provider), try to recreate.
                    # project_id_label=self.project_id: ensures Daytona uses project_id for tagging if it creates one.
                    self._sandbox = get_sandbox(sandbox_id=self._sandbox_id, auto_create=True, project_id_label=self.project_id)
                
                except Exception as e:
                    logger.error(f"Error retrieving sandbox from DB for project {self.project_id}: {str(e)}", exc_info=True)
                    raise # Re-raise the original error after logging

            # Common step after sandbox is obtained (either via DB or direct)
            if self._sandbox:
                self._sandbox.start() # Ensure the sandbox environment is started
                logger.info(f"Sandbox for project {self.project_id} (ID/Name: {getattr(self._sandbox, 'sandbox_id', getattr(self._sandbox, 'container_name', 'N/A'))}) ensured and started.")
                # # Log URLs if not already printed - This part needs to be adapted for AbstractSandbox
                # if not SandboxToolsBase._urls_printed:
                # try:
                #     vnc_link_obj = self._sandbox.get_preview_link(6080) # Standard VNC port
                #     # website_link_obj = self._sandbox.get_preview_link(8080) # Example app port

                #     vnc_url = vnc_link_obj.url if hasattr(vnc_link_obj, 'url') else str(vnc_link_obj)
                #     # website_url = website_link_obj.url if hasattr(website_link_obj, 'url') else str(website_link_obj)

                #     print("\033[95m*** Sandbox Access (Project: ", self.project_id, ") ***")
                #     print(f"VNC URL: {vnc_url} (Password: {self._sandbox_pass or 'Not set'})")
                #     # print(f"Example App URL: {website_url}")
                #     print("***\033[0m")
                #     SandboxToolsBase._urls_printed = True # Print once per class lifecycle (not instance)
                # except Exception as e:
                #     logger.error(f"Failed to get preview links for sandbox: {e}")

            else:
                # This should ideally not be reached if get_sandbox raises an error on failure.
                raise RuntimeError(f"Failed to obtain a sandbox instance for project {self.project_id}.")

        return self._sandbox
    # End of _ensure_sandbox method

    @property
    def sandbox(self) -> AbstractSandbox: # Updated return type hint
        """Get the sandbox instance, ensuring it exists."""
        if self._sandbox is None:
            # This typically means _ensure_sandbox was not awaited.
            # For synchronous access, this property might not be suitable if _ensure_sandbox is async.
            # However, tools using this property should have already called and awaited _ensure_sandbox.
            raise RuntimeError("Sandbox not initialized. Call and await _ensure_sandbox() first in an async context.")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        """Get the sandbox ID, ensuring it exists."""
        if self._sandbox_id is None:
            raise RuntimeError("Sandbox ID not initialized. Call _ensure_sandbox() first.")
        return self._sandbox_id

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace."""
        cleaned_path = clean_path(path, self.workspace_path)
        logger.debug(f"Cleaned path: {path} -> {cleaned_path}")
        return cleaned_path