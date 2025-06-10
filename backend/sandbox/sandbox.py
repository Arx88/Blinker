import os
from .abs_sandbox import AbstractSandbox
from .docker_sandbox import DockerSandbox
from .daytona_sandbox import DaytonaSandbox # Make sure this import is correct

# Attempt to import utils for logging, but make it optional for basic factory functioning
try:
    from utils.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    # Configure basic logging if utils.logger is not available
    if not logger.handlers: # Avoid adding multiple handlers if this block is re-entered
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    logger.info("utils.logger not found, using standard logging for sandbox factory.")


def get_sandbox(sandbox_id: str = None, auto_create: bool = False, project_id_label: str = None) -> AbstractSandbox:
    '''
    Factory function to get the appropriate sandbox implementation
    based on the BLINKER_SETUP_MODE environment variable.

    Args:
        sandbox_id (str, optional): The ID of an existing sandbox to connect to.
                                      For DockerSandbox, this can be the container name.
        auto_create (bool, optional): If True, and sandbox_id is not found or not provided,
                                      a new sandbox may be created. Defaults to False.
                                      (Mainly relevant for DaytonaSandbox)
        project_id_label (str, optional): A label used to identify or create the sandbox,
                                          useful for Daytona to find/tag sandboxes.
                                          (Mainly relevant for DaytonaSandbox)
    '''
    setup_mode = os.environ.get("BLINKER_SETUP_MODE", "local") # Default to local
    logger.info(f"BLINKER_SETUP_MODE='{setup_mode}', determining sandbox type...")

    if setup_mode == "daytona":
        logger.info("Attempting to use DaytonaSandbox.")
        try:
            # Ensure DaytonaSandbox can be initialized without error if critical utils are missing
            # This might involve checking for daytona_sdk presence earlier or handling it in DaytonaSandbox init
            return DaytonaSandbox(sandbox_id=sandbox_id, auto_create=auto_create, project_id_label=project_id_label)
        except ImportError as ie:
            logger.error(f"ImportError for DaytonaSandbox dependencies (e.g., daytona_sdk, utils): {ie}")
            raise RuntimeError(f"DaytonaSDK or utils missing, cannot use DaytonaSandbox: {ie}") from ie
        except Exception as e:
            logger.error(f"Failed to initialize DaytonaSandbox: {e}")
            raise RuntimeError(f"Failed to initialize DaytonaSandbox as per BLINKER_SETUP_MODE: {e}") from e

    elif setup_mode == "local":
        logger.info("Attempting to use DockerSandbox.")
        container_name_to_use = sandbox_id if sandbox_id else os.environ.get("DOCKER_SANDBOX_CONTAINER_NAME")
        # If no sandbox_id (container_name) is provided, DockerSandbox uses its own default "blinker_sandbox_dev"
        try:
            if container_name_to_use:
                return DockerSandbox(container_name=container_name_to_use)
            else:
                return DockerSandbox() # Uses default container name
        except ImportError as ie:
             logger.error(f"ImportError for DockerSandbox dependencies (e.g., docker lib): {ie}")
             raise RuntimeError(f"Docker library missing, cannot use DockerSandbox: {ie}") from ie
        except Exception as e:
            logger.error(f"Failed to initialize DockerSandbox: {e}")
            raise RuntimeError(f"Failed to initialize DockerSandbox: {e}") from e

    else: # Unknown mode
        logger.warning(f"Unknown BLINKER_SETUP_MODE '{setup_mode}'. Defaulting to DockerSandbox.")
        container_name_to_use = sandbox_id if sandbox_id else os.environ.get("DOCKER_SANDBOX_CONTAINER_NAME")
        try:
            if container_name_to_use:
                return DockerSandbox(container_name=container_name_to_use)
            else:
                return DockerSandbox()
        except ImportError as ie:
             logger.error(f"ImportError for DockerSandbox dependencies (default): {ie}")
             raise RuntimeError(f"Docker library missing, cannot use DockerSandbox (default): {ie}") from ie
        except Exception as e:
            logger.error(f"Failed to initialize DockerSandbox (default): {e}")
            raise RuntimeError(f"Failed to initialize DockerSandbox (default): {e}") from e

# Example usage (commented out, for testing):
# if __name__ == '__main__':
#     # Store original mode for restoration
#     original_mode = os.environ.get('BLINKER_SETUP_MODE')
#     try:
#         print(f"Testing get_sandbox function...")

#         # Test local Docker mode
#         print("\n--- Testing LOCAL mode ---")
#         os.environ['BLINKER_SETUP_MODE'] = 'local'
#         # For Docker, you might need a running container named "test_factory_sandbox"
#         # e.g., docker run -d --name test_factory_sandbox alpine tail -f /dev/null
#         # Ensure this container exists, or DockerSandbox will fail to start if it tries to get it by a specific name not running
#         # If using default name in DockerSandbox ("blinker_sandbox_dev"), ensure that's running.
#         # For this test, let's assume the default "blinker_sandbox_dev" or one specified by DOCKER_SANDBOX_CONTAINER_NAME is NOT required to exist beforehand
#         # as DockerSandbox.start() might try to create it or use a predefined one from a compose file.
#         # However, current DockerSandbox.start() TRIES to GET an existing container.
#         # So, for this test to pass for 'local', a container (e.g. "blinker_sandbox_dev" or one from env var) must exist.
#         # To make it runnable without pre-existing container:
#         # 1. Create a dummy container: `docker run -d --name blinker_sandbox_dev alpine tail -f /dev/null`
#         try:
#             local_sandbox = get_sandbox() # Uses default name in DockerSandbox or env var
#             print(f"Got local sandbox: {type(local_sandbox)}")
#             local_sandbox.start() # Tries to connect to 'blinker_sandbox_dev' by default
#             code, output = local_sandbox.execute_command("echo Hello from local sandbox")
#             print(f"Local echo: {code} - {output}")
#             # Clean up dummy container: `docker rm -f blinker_sandbox_dev`
#         except Exception as e:
#             print(f"Local mode test failed: {e}. This might be due to Docker not running, or container not found.")
#             print("If container not found, try: docker run -d --name blinker_sandbox_dev alpine tail -f /dev/null")


#         # Test Daytona mode (requires Daytona setup and utils.logger/config)
#         print("\n--- Testing DAYTONA mode ---")
#         # Ensure DAYTONA_API_KEY, DAYTONA_SERVER_URL, DAYTONA_TARGET are set in .env
#         # and that utils.logger/config are in PYTHONPATH
#         # Also, daytona_sdk must be installed.
#         os.environ['BLINKER_SETUP_MODE'] = 'daytona'
#         # This test will try to create a new sandbox if API keys are valid.
#         # import uuid
#         # test_daytona_project_id = f"factory_test_{uuid.uuid4().hex[:8]}"
#         # try:
#         #     daytona_sandbox_instance = get_sandbox(auto_create=True, project_id_label=test_daytona_project_id)
#         #     print(f"Got Daytona sandbox: {type(daytona_sandbox_instance)}")
#         #     daytona_sandbox_instance.start()
#         #     code, output = daytona_sandbox_instance.execute_command("echo Hello from Daytona")
#         #     print(f"Daytona echo: {code} - {output}")
#         #     if hasattr(daytona_sandbox_instance, 'delete'):
#         #        daytona_sandbox_instance.delete()
#         #        print(f"Cleaned up Daytona sandbox for project: {test_daytona_project_id}")
#         # except Exception as e:
#         #     print(f"Daytona mode test failed: {e}. Check Daytona config, server, and SDK installation.")
#         #     print("Ensure DAYTONA_API_KEY, DAYTONA_SERVER_URL, DAYTONA_TARGET are set.")
#         #     print("And 'utils' path is correct for logger/config, and 'daytona_sdk' is installed.")

#     except RuntimeError as re:
#         print(f"Runtime error during sandbox testing: {re}")
#     except ImportError as ie:
#         print(f"Import error during factory test setup: {ie}")
#     except Exception as e:
#         print(f"An unexpected error occurred during factory test: {e}")
#     finally:
#         # Restore original mode
#         if original_mode is None:
#             os.environ.pop('BLINKER_SETUP_MODE', None)
#         else:
#             os.environ['BLINKER_SETUP_MODE'] = original_mode
#         print("\nTest finished.")
