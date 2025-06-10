from abc import ABC, abstractmethod
from typing import Any # For get_preview_link return type

class AbstractSandbox(ABC):
    @abstractmethod
    def start(self) -> None:
        '''Starts the sandbox environment.'''
        pass

    @abstractmethod
    def stop(self) -> None:
        '''Stops the sandbox environment.'''
        pass

    @abstractmethod
    def execute_command(self, command: str) -> tuple[int, str]:
        '''
        Executes a command inside the sandbox.
        Returns a tuple of (exit_code, output_string).
        '''
        pass

    @abstractmethod
    def get_preview_link(self, port: int) -> Any: # Return type might be a specific object or str
        '''Gets a preview link for a given port in the sandbox.'''
        pass

    # Consider adding other common methods if apparent from existing sandbox,
    # e.g., upload_file, download_file, etc. For now, stick to the spec.
