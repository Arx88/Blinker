import docker
from .abs_sandbox import AbstractSandbox

class DockerSandbox(AbstractSandbox):
    def __init__(self, container_name="blinker_sandbox_dev"): # Or make configurable
        self.client = docker.from_env()
        self.container_name = container_name
        self.container = None # Will be initialized in start()

    def start(self) -> None:
        '''
        Starts a pre-defined Docker container for the sandbox.
        Assumes a Docker image/container is defined elsewhere (e.g., in a docker-compose.yml for the sandbox itself).
        For now, this will try to find an existing container or could be expanded to run one.
        '''
        print(f"Attempting to connect to or start Docker container: {self.container_name}")
        try:
            self.container = self.client.containers.get(self.container_name)
            if self.container.status != "running":
                print(f"Container '{self.container_name}' found but not running. Starting it...")
                self.container.start()
                print(f"Container '{self.container_name}' started.")
            else:
                print(f"Container '{self.container_name}' is already running.")
        except docker.errors.NotFound:
            # This is a critical decision point:
            # Option A: Raise an error, expecting 'docker-compose up' from setup.py to have created it.
            # Option B: Try to run a default sandbox container (e.g., from a predefined image).
            # For now, let's go with Option A as it aligns with setup.py managing the sandbox container.
            print(f"Error: Docker container '{self.container_name}' not found. It should be started by the main Blinker setup (e.g., docker-compose).")
            raise # Re-raise the NotFound error or a custom one.
        except docker.errors.APIError as e:
            print(f"Error connecting to Docker or operating on container: {e}")
            raise

    def stop(self) -> None:
        '''Stops the managed Docker container (if desired, or maybe this is a no-op if managed externally).'''
        # Decision: Should the sandbox instance stop its container, or is it managed by docker-compose?
        # For now, let's assume it can be stopped individually if needed, but setup.py will handle the main up/down.
        if self.container:
            try:
                print(f"Stopping container '{self.container_name}'...")
                self.container.stop()
                print(f"Container '{self.container_name}' stopped.")
            except docker.errors.APIError as e:
                print(f"Error stopping container '{self.container_name}': {e}")
                # Don't raise, just log, as it might already be stopped.
        else:
            print("No active container to stop for this DockerSandbox instance.")

    def execute_command(self, command: str) -> tuple[int, str]:
        '''Executes a command in the running Docker container.'''
        if not self.container or self.container.status != "running":
            # Try to re-initialize/start if not running
            print("Container not running or not initialized. Attempting to start/connect...")
            try:
                self.start() # Attempt to connect/start
            except Exception as e:
                return -1, f"Failed to start or connect to container before command execution: {e}"

            if not self.container or self.container.status != "running":
                 return -1, "Sandbox container is not running. Cannot execute command."


        print(f"Executing in Docker container '{self.container_name}': {command}")
        try:
            # Ensure command is a list of strings if your Docker SDK version prefers it or for clarity
            # For many versions, a string is fine.
            cmd_parts = command.split() # Basic split, might need shlex for complex commands
            exit_code, output_tuple = self.container.exec_run(cmd_parts) # output_tuple is (stdout, stderr) for newer SDKs

            # Handle older docker SDK returning just output bytes
            if isinstance(output_tuple, bytes):
                output_str = output_tuple.decode('utf-8', errors='replace')
            elif isinstance(output_tuple, tuple) and len(output_tuple) == 2: # (stdout_bytes, stderr_bytes)
                stdout_bytes, stderr_bytes = output_tuple
                output_str = ""
                if stdout_bytes:
                    output_str += stdout_bytes.decode('utf-8', errors='replace')
                if stderr_bytes: # Include stderr in the output string for simplicity here
                    output_str += stderr_bytes.decode('utf-8', errors='replace')
            else: # Fallback for unknown format
                 output_str = str(output_tuple)

            return exit_code, output_str.strip()
        except docker.errors.APIError as e:
            print(f"Error executing command in Docker container: {e}")
            return -1, str(e)

    def get_preview_link(self, port: int) -> str: # Assuming it returns a string URL
        # This implementation assumes that the sandbox container's ports are mapped 1:1 to localhost
        # on the machine where the Docker daemon is running, and that this is accessible
        # by the entity that needs the preview link (e.g., the user's browser on the same machine,
        # or another service that can reach localhost of the Docker host).

        # If the Blinker backend (which calls this) runs on the Docker host, 'localhost' is correct.
        # If Blinker backend runs in a container, and the sandbox is another container,
        # they might communicate via Docker network names. However, this link is usually for
        # external access (e.g., user's browser).

        # A more robust solution for Docker might involve:
        # 1. Inspecting `self.container.ports` to find the host port mapped to the container's `port`.
        # 2. Using a known host IP/domain instead of 'localhost' if access is from outside the Docker host.
        #    This could come from a configuration (e.g., os.environ.get("APP_HOST_URL", "http://localhost")).

        # For now, a simple localhost URL structure is provided.
        # This aligns with typical local development where services are exposed on localhost.

        # Example: If container port 80 is mapped to host port 8080, this should return http://localhost:8080.
        # The current simple version assumes the port `port` is directly accessible on localhost.

        try:
            if not self.container:
                self.start() # Ensure container is loaded
            if not self.container: # Still no container after trying to start
                 raise RuntimeError("Container not available for getting preview link.")

            # Refresh container attributes, especially ports
            self.container.reload()

            # Construct the key for the ports dictionary (e.g., "80/tcp")
            port_key = f"{port}/tcp" # Assume TCP, common for web services

            if self.container.ports and port_key in self.container.ports:
                host_mappings = self.container.ports[port_key]
                if host_mappings:
                    # Typically, for a published port, there's a list of mappings.
                    # We'll take the first one. Format is usually {'HostIp': '0.0.0.0', 'HostPort': 'XXXX'}
                    host_port = host_mappings[0]['HostPort']
                    # Use 'localhost' as HostIp '0.0.0.0' means accessible on all host interfaces.
                    # If a specific HostIp other than '0.0.0.0' or '::' is set, that could be used,
                    # but 'localhost' is generally safer for links intended for the user's machine.
                    return f"http://localhost:{host_port}"

            # Fallback if port not specifically mapped or inspectable in this simple way
            # This assumes the port inside the container is directly addressable via localhost,
            # which is true if --network="host" is used, or for some docker-compose setups
            # where service names resolve within the Docker network but links for users need localhost.
            print(f"Warning: Port {port} not found in explicit mappings for container {self.container_name}. Defaulting to http://localhost:{port}. This might not be correct if ports are remapped by Docker.")
            return f"http://localhost:{port}"

        except Exception as e:
            print(f"Error getting preview link for port {port} in container {self.container_name}: {e}")
            # Fallback to a generic localhost URL, but this is likely not correct if an error occurred.
            return f"http://localhost:{port}"


# Example Usage (for testing, can be removed later)
if __name__ == '__main__':
    print("Testing DockerSandbox (requires a running container named 'blinker_sandbox_dev' or adjust name)...")
    # You would need a container running, e.g., from a simple Dockerfile:
    # FROM alpine
    # CMD ["tail", "-f", "/dev/null"]
    # And run as: docker run -d --name blinker_sandbox_dev alpine tail -f /dev/null

    # For this test, we'll assume the container might not exist and handle it.
    try:
        # IMPORTANT: Replace 'blinker_sandbox_dev' with the actual name of your backend service container
        # as defined in your main docker-compose.yaml, or a dedicated sandbox container name.
        # For instance, if your docker-compose service is named 'backend', the container might be 'blinker-backend-1' or similar.
        # You can find the name using 'docker ps'.
        # For initial testing, a simple container like 'docker run -d --name test_sandbox alpine tail -f /dev/null' can be used.
        sandbox = DockerSandbox(container_name="test_sandbox") # ADJUST THIS NAME for your test environment
        sandbox.start()

        print("\n--- Testing command execution ---")
        exit_code, output = sandbox.execute_command("echo Hello from Docker Sandbox")
        print(f"Exit Code: {exit_code}")
        print(f"Output:\n{output}")

        exit_code, output = sandbox.execute_command("ls -la /")
        print(f"Exit Code: {exit_code}")
        print(f"Output:\n{output}")

        exit_code, output = sandbox.execute_command("pwd")
        print(f"Exit Code: {exit_code}")
        print(f"Output:\n{output}")

        # Test non-existent command
        exit_code, output = sandbox.execute_command("non_existent_command_test_123")
        print(f"Exit Code for non_existent_command_test: {exit_code}")
        print(f"Output:\n{output}") # Should show error from shell, e.g., "sh: non_existent_command_test_123: not found"

        # sandbox.stop() # Optional: stop it if you started it here for test (not recommended if it's a shared dev container)
    except docker.errors.NotFound:
        print(f"\nDockerSandbox test skipped: Container for sandbox (e.g., 'test_sandbox') not found.")
        print("To run this test, ensure a Docker container with this name is running.")
        print("Example: 'docker run -d --name test_sandbox alpine tail -f /dev/null'")
        print("Then adjust DockerSandbox(container_name=...) to match.")
    except docker.errors.DockerException as de:
        print(f"\nDockerSandbox test failed: Docker is not running or not accessible: {de}")
        print("Ensure Docker daemon is running.")
    except Exception as e:
        print(f"\nAn unexpected error occurred during DockerSandbox test: {e}")
        print("This could be due to various issues, including incorrect Docker setup, container state, or permissions.")

# Note: The `docker` Python library is required. Ensure it's in your backend/requirements.txt:
# docker
# And run `pip install -r backend/requirements.txt` or `mise install` if it handles pip installs.
