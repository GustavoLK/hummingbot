"""
Debug configuration module for GLK project.
This module provides functionality to enable remote debugging when PYTHONDEV=1
is set in the environment.
"""

import os
from typing import Optional, Tuple


def enable_debugging(host: str = "0.0.0.0", port: int = 5678) -> Optional[Tuple[str, int]]:
    """
    Enable remote debugging if PYTHONDEV=1 is set in the environment.

    Args:
        host (str): The host to listen on. Defaults to "0.0.0.0"
        port (int): The port to listen on. Defaults to 5678

    Returns:
        Optional[Tuple[str, int]]: The host and port if debugging was enabled,
                                  None otherwise
    """
    if os.environ.get('PYTHONDEV') == '1':
        try:
            import debugpy
            debugpy.listen((host, port))
            debugpy.wait_for_client()
            return host, port
        except RuntimeError as e:
            if "listen() has already been called" in str(e):
                print("Debugpy is already listening, continuing...")
                return host, port
            else:
                raise  # Re-raise if it's a different RuntimeError
    return None
