from deepagents.backends import LocalShellBackend

from agent.utils import config as cfg


def create_local_sandbox(sandbox_id: str | None = None):
    """Create a local shell sandbox with no isolation.

    WARNING: This runs commands directly on the host machine with no sandboxing.
    Only use for local development with human-in-the-loop enabled.

    The root directory can be set via LOCAL_SANDBOX_ROOT_DIR in config.py.

    Args:
        sandbox_id: Ignored for local sandboxes; accepted for interface compatibility.

    Returns:
        LocalShellBackend instance implementing SandboxBackendProtocol.
    """
    root_dir = cfg.LOCAL_SANDBOX_ROOT_DIR

    return LocalShellBackend(
        root_dir=root_dir,
        inherit_env=True,
        virtual_mode=True,
    )
