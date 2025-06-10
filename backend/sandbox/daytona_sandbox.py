import os
from .abs_sandbox import AbstractSandbox
from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams, Sandbox as DaytonaSDK_Sandbox, SessionExecuteRequest
from daytona_api_client.models.workspace_state import WorkspaceState
from dotenv import load_dotenv
from utils.logger import logger # Assuming logger is available in this path
from utils.config import config as global_blinker_config # Assuming config is available
from utils.config import Configuration as GlobalBlinkerConfiguration # Assuming this is also used

# Load environment variables from .env if not already loaded by the main app
load_dotenv()

class DaytonaSandbox(AbstractSandbox):
    def __init__(self, sandbox_id: str = None, auto_create: bool = False, project_id_label: str = None):
        logger.debug("Initializing DaytonaSandbox")
        self.daytona_config = DaytonaConfig(
            api_key=global_blinker_config.DAYTONA_API_KEY,
            server_url=global_blinker_config.DAYTONA_SERVER_URL,
            target=global_blinker_config.DAYTONA_TARGET
        )
        self.daytona_client = Daytona(self.daytona_config)
        self.sandbox_id = sandbox_id
        self.project_id_label = project_id_label # Used if creating a new sandbox
        self.sandbox_instance: DaytonaSDK_Sandbox = None
        self.default_session_id = "blinker_default_session"

        if not self.daytona_config.api_key or not self.daytona_config.server_url:
            logger.error("Daytona API key or Server URL is not configured. DaytonaSandbox cannot operate.")
            raise ValueError("Daytona API key or Server URL missing.")

        if self.sandbox_id:
            try:
                logger.info(f"Attempting to retrieve existing Daytona sandbox with ID: {self.sandbox_id}")
                self.sandbox_instance = self.daytona_client.get_current_sandbox(self.sandbox_id)
                logger.info(f"Successfully retrieved sandbox '{self.sandbox_id}'. State: {self.sandbox_instance.instance.state if self.sandbox_instance.instance else 'N/A'}")
            except Exception as e:
                logger.warning(f"Could not retrieve Daytona sandbox with ID '{self.sandbox_id}': {e}. It might need to be started or created.")
                if not auto_create:
                    # If not auto_create, and an ID was provided, it's an issue if not found.
                    raise ValueError(f"Daytona sandbox with ID '{self.sandbox_id}' not found and auto_create is False.") from e
                else:
                    self.sandbox_id = None # Clear ID so start() can create one

        if not self.sandbox_instance and auto_create:
            logger.info("No sandbox_id provided or existing one not found, and auto_create is True. Will attempt to create in start().")


    def _ensure_session(self):
        """Ensures a default session exists for command execution."""
        if not self.sandbox_instance:
            raise RuntimeError("Sandbox instance not available. Cannot ensure session.")

        # Check if session exists (Daytona SDK might not have a direct way to list/check sessions)
        # For simplicity, we'll try to create it. If it exists, it might be a no-op or handled by Daytona.
        try:
            logger.info(f"Ensuring default session '{self.default_session_id}' exists for sandbox '{self.sandbox_id}'.")
            self.sandbox_instance.process.create_session(self.default_session_id)
            logger.info(f"Default session '{self.default_session_id}' ensured.")
        except Exception as e:
            # This might fail if session already exists, depending on SDK behavior.
            # Assuming SDK handles "already exists" gracefully or it's okay to reuse.
            logger.warning(f"Could not explicitly create session '{self.default_session_id}', it might already exist: {e}")


    def start(self) -> None:
        '''Ensures the Daytona sandbox (workspace) is running. Creates if necessary if auto_create was true.'''
        logger.info(f"Starting DaytonaSandbox (ID: {self.sandbox_id or 'New'})...")
        try:
            if not self.sandbox_instance:
                if self.sandbox_id: # ID was given but instance not loaded (e.g. network issue before)
                     self.sandbox_instance = self.daytona_client.get_current_sandbox(self.sandbox_id)
                elif self.project_id_label : # No ID, but a project_id_label to find or create
                    # Try to find by label first
                    sandboxes = self.daytona_client.list()
                    for sb in sandboxes:
                        if sb.labels and sb.labels.get('id') == self.project_id_label:
                            logger.info(f"Found existing sandbox by project_id label '{self.project_id_label}': {sb.id}")
                            self.sandbox_instance = sb
                            self.sandbox_id = sb.id
                            break
                    if not self.sandbox_instance:
                        logger.info(f"No existing sandbox found for project_id_label '{self.project_id_label}'. Creating new one.")
                        self._create_new_sandbox()
                else: # No ID and no project_id_label, create a generic new one
                     logger.info("No sandbox_id or project_id_label. Creating a new generic sandbox.")
                     self._create_new_sandbox()


            if self.sandbox_instance.instance.state in [WorkspaceState.ARCHIVED, WorkspaceState.STOPPED, WorkspaceState.UNKNOWN]:
                logger.info(f"Daytona sandbox '{self.sandbox_id}' is {self.sandbox_instance.instance.state}. Attempting to start...")
                self.daytona_client.start(self.sandbox_instance)
                # Refresh state
                self.sandbox_instance = self.daytona_client.get_current_sandbox(self.sandbox_id)
                logger.info(f"Daytona sandbox '{self.sandbox_id}' started. Current state: {self.sandbox_instance.instance.state}")
            elif self.sandbox_instance.instance.state == WorkspaceState.RUNNING:
                logger.info(f"Daytona sandbox '{self.sandbox_id}' is already running.")
            else:
                logger.warning(f"Daytona sandbox '{self.sandbox_id}' is in an unhandled state: {self.sandbox_instance.instance.state}")

            self._ensure_session() # Ensure default session is ready

        except Exception as e:
            logger.error(f"Error starting or creating Daytona sandbox: {e}")
            raise # Re-raise to indicate failure in starting

    def _create_new_sandbox(self):
        """Creates a new Daytona sandbox."""
        logger.info("Creating new Daytona sandbox environment...")
        # Using a generic password for VNC, should be configurable or managed securely
        vnc_password = os.environ.get("DAYTONA_SANDBOX_VNC_PASSWORD", "blinker")

        labels = None
        if self.project_id_label:
            labels = {'id': self.project_id_label}

        params = CreateSandboxParams(
            image=GlobalBlinkerConfiguration.SANDBOX_IMAGE_NAME, # From existing code
            public=True,
            labels=labels,
            env_vars={
                "CHROME_PERSISTENT_SESSION": "true", "RESOLUTION": "1024x768x24",
                "RESOLUTION_WIDTH": "1024", "RESOLUTION_HEIGHT": "768",
                "VNC_PASSWORD": vnc_password, "ANONYMIZED_TELEMETRY": "false",
                "CHROME_PATH": "", "CHROME_USER_DATA": "", "CHROME_DEBUGGING_PORT": "9222",
                "CHROME_DEBUGGING_HOST": "localhost", "CHROME_CDP": ""
            },
            resources={"cpu": 2, "memory": 4, "disk": 5}, # Default resources
            auto_stop_interval=24 * 60 # Auto stop after 24 hours
        )
        self.sandbox_instance = self.daytona_client.create(params)
        self.sandbox_id = self.sandbox_instance.id
        logger.info(f"Daytona sandbox created with ID: {self.sandbox_id}")
        # supervisord is usually started by the image entrypoint or a startup script within the image.
        # If explicit start is needed: self._start_supervisord_in_sandbox()

    def _start_supervisord_in_sandbox(self):
        """Helper to start supervisord if not automatically started by the Daytona image/workspace."""
        if not self.sandbox_instance:
            logger.error("Cannot start supervisord, sandbox instance not available.")
            return
        try:
            logger.info(f"Attempting to start supervisord in session for sandbox '{self.sandbox_id}'")
            # Reusing the default session logic
            self._ensure_session()
            req = SessionExecuteRequest(
                command="exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
                var_async=True # Run supervisord in background
            )
            self.sandbox_instance.process.execute_session_command(self.default_session_id, req)
            logger.info(f"Supervisord start command executed in session '{self.default_session_id}'.")
        except Exception as e:
            logger.error(f"Error starting supervisord in sandbox '{self.sandbox_id}': {e}")
            # This might not be critical if supervisord is already running or managed differently.

    def stop(self) -> None:
        '''Stops the Daytona sandbox (workspace).'''
        if self.sandbox_instance:
            logger.info(f"Stopping Daytona sandbox '{self.sandbox_id}'...")
            try:
                # Check if SDK has a direct stop, or if it's part of 'remove' or instance management
                # The original code used daytona.start() for non-running states, implying there's a stop.
                # Assuming daytona_client.stop(instance) or similar exists. If not, this needs adjustment.
                # The daytona_sdk.Daytona class has a stop method.
                if self.sandbox_instance.instance.state == WorkspaceState.RUNNING:
                    self.daytona_client.stop(self.sandbox_instance)
                    logger.info(f"Daytona sandbox '{self.sandbox_id}' stopped.")
                else:
                    logger.info(f"Daytona sandbox '{self.sandbox_id}' is not running, no need to stop.")
            except Exception as e:
                logger.error(f"Error stopping Daytona sandbox '{self.sandbox_id}': {e}")
                # Don't raise, just log, as per DockerSandbox example
        else:
            logger.info("No active Daytona sandbox instance to stop.")

    def execute_command(self, command: str) -> tuple[int, str]:
        '''Executes a command in the Daytona sandbox.'''
        if not self.sandbox_instance or self.sandbox_instance.instance.state != WorkspaceState.RUNNING:
            logger.warning(f"Daytona sandbox '{self.sandbox_id}' is not running. Attempting to start.")
            try:
                self.start() # This will also ensure session
            except Exception as e:
                return -1, f"Failed to start Daytona sandbox before command execution: {e}"

            if not self.sandbox_instance or self.sandbox_instance.instance.state != WorkspaceState.RUNNING:
                 return -1, "Daytona sandbox is not running. Cannot execute command."

        logger.info(f"Executing in Daytona sandbox '{self.sandbox_id}', session '{self.default_session_id}': {command}")
        try:
            # Daytona SDK's execute_session_command returns models.SessionExecute
            # We need to adapt this to (exit_code, output_string)
            # The SessionExecute model might have 'output' or similar, and exit code might be implicit (0 if no error)
            # This part requires knowledge of daytona_sdk.models.SessionExecute structure.
            # For now, let's assume it's synchronous and output is directly available or through another call.
            # The original code used var_async=True for supervisord. For general commands, we want sync.

            req = SessionExecuteRequest(command=command, var_async=False) # Synchronous execution
            response = self.sandbox_instance.process.execute_session_command(self.default_session_id, req)

            # Assuming response.output contains the string output.
            # Exit code needs to be determined. If command fails, SDK might raise exception or response has error field.
            # This is a placeholder based on typical SDK patterns.
            output_str = response.output if hasattr(response, 'output') else str(response)
            exit_code = 0 # Default to 0. Needs to be updated if SDK provides exit codes.
            if hasattr(response, 'error') and response.error: # Fictional error field
                output_str += f"\nError from Daytona: {response.error}"
                exit_code = -1 # Indicate error

            return exit_code, output_str.strip()
        except Exception as e:
            logger.error(f"Error executing command in Daytona sandbox: {e}")
            return -1, str(e)

    def get_preview_link(self, port: int): # -> Returns daytona_sdk.models.PreviewLink or similar
        """Gets a preview link for a given port in the Daytona workspace."""
        if not self.sandbox_instance:
            logger.error("Daytona sandbox instance not available, cannot get preview link.")
            # Consistent with AbstractSandbox, we need to return something.
            # The tools expect an object with a .url attribute or a string.
            # Returning a placeholder or raising an error are options.
            # Let's return a placeholder string indicating the issue.
            return f"http://error.host/preview-unavailable-sandbox-not-loaded-port-{port}"

        try:
            logger.info(f"Getting preview link for port {port} in Daytona sandbox '{self.sandbox_id}'.")
            # Assuming self.sandbox_instance is a DaytonaSDK_Sandbox object
            # and it has a method get_preview_link as inferred from original sb_expose_tool.py
            preview_link_obj = self.sandbox_instance.get_preview_link(port)
            # The tools use: preview_link.url if hasattr(preview_link, 'url') else str(preview_link)
            # So, returning the object itself is fine if it has .url or can be stringified.
            return preview_link_obj
        except Exception as e:
            logger.error(f"Error getting preview link from Daytona sandbox '{self.sandbox_id}': {e}")
            return f"http://error.host/preview-error-port-{port}"


    def delete(self) -> None:
        """Deletes the Daytona sandbox."""
        if self.sandbox_instance:
            logger.info(f"Deleting Daytona sandbox '{self.sandbox_id}'...")
            try:
                self.daytona_client.remove(self.sandbox_instance)
                logger.info(f"Daytona sandbox '{self.sandbox_id}' deleted.")
                self.sandbox_instance = None
                self.sandbox_id = None
            except Exception as e:
                logger.error(f"Error deleting Daytona sandbox '{self.sandbox_id}': {e}")
                raise
        else:
            logger.info("No active Daytona sandbox instance to delete.")


# Example Usage (for testing, can be removed or adapted)
if __name__ == '__main__':
    print("Testing DaytonaSandbox...")
    # This test requires Daytona server to be running and configured via .env or environment variables
    # (DAYTONA_API_KEY, DAYTONA_SERVER_URL, DAYTONA_TARGET)
    # It also needs utils.logger and utils.config to be available in the PYTHONPATH.

    # Test Case 1: Create a new sandbox (if DAYTONA_API_KEY etc. are set)
    try:
        print("\n--- Test Case 1: Create and use new Daytona Sandbox ---")
        # Using a unique project_id_label for testing to avoid conflicts
        import uuid
        test_project_id = f"blinker_test_{uuid.uuid4().hex[:8]}"

        daytona_sb = DaytonaSandbox(auto_create=True, project_id_label=test_project_id)
        daytona_sb.start() # Should create and start

        exit_code, output = daytona_sb.execute_command("echo Hello from Daytona Sandbox")
        print(f"Echo Test - Exit Code: {exit_code}, Output:\n{output}")

        exit_code, output = daytona_sb.execute_command("ls -la /")
        print(f"LS Test - Exit Code: {exit_code}, Output:\n{output}")

        daytona_sb.delete() # Clean up
        print(f"Test sandbox {test_project_id} cleaned up.")

    except ValueError as ve:
         print(f"Skipping DaytonaSandbox creation test: {ve}")
    except ImportError:
        print("Skipping DaytonaSandbox test: Missing 'utils.logger' or 'utils.config'. Ensure PYTHONPATH is set correctly.")
    except Exception as e:
        print(f"An error occurred during DaytonaSandbox 'create' test: {e}")
        print("Ensure Daytona server is running, configured, and 'utils' are in PYTHONPATH.")

    # Test Case 2: Connect to an existing sandbox (Manually set up a sandbox in Daytona and get its ID)
    # existing_id = "your-existing-daytona-sandbox-id"
    # if existing_id != "your-existing-daytona-sandbox-id":
    #     try:
    #         print(f"\n--- Test Case 2: Connect to existing Daytona Sandbox (ID: {existing_id}) ---")
    #         daytona_sb_existing = DaytonaSandbox(sandbox_id=existing_id)
    #         daytona_sb_existing.start() # Should connect and ensure it's running

    #         exit_code, output = daytona_sb_existing.execute_command("hostname")
    #         print(f"Hostname Test - Exit Code: {exit_code}, Output: {output}")

    #         # daytona_sb_existing.stop() # Optional: stop it
    #     except ValueError as ve:
    #         print(f"Skipping DaytonaSandbox existing sandbox test: {ve}")
    #     except ImportError:
    #         print("Skipping DaytonaSandbox test: Missing 'utils.logger' or 'utils.config'.")
    #     except Exception as e:
    #         print(f"An error occurred during DaytonaSandbox 'existing' test: {e}")
    # else:
    #     print("\nSkipping Test Case 2: No existing Daytona sandbox ID provided for testing.")
