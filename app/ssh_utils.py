import asyncio
import logging
import os

import asyncssh

from . import config  # Import config settings

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# SSH connection options (can be customized further if needed)
# Known hosts checking is disabled for simplicity here, but consider enabling
# it in production by providing a known_hosts file or callback.
CONNECTION_OPTIONS = asyncssh.SSHClientConnectionOptions(
    known_hosts=None,  # Disables host key checking
    connect_timeout=10,
    # You might need client_keys=[private_key_obj] if auto-discovery fails
)


async def run_ssh_command_async(host_alias: str, command: str) -> tuple[int, str | None, str | None]:
    """Execute a command on a remote host via asyncssh, handling jump hosts.

    Args:
        host_alias: The alias of the target machine (key in config.settings.host_details).
        command: The command string to execute.

    Returns:
        A tuple containing:
        - return_code (int): The exit code of the command (0 for success, -1 for connection error,
                           -2 for config error, -3 for key error, -4 for timeout, -5 other error).
        - stdout (Optional[str]): The standard output, or None on error.
        - stderr (Optional[str]): The standard error, or None on error.

    """
    logger.info("Attempting SSH command on alias '%s': %s", host_alias, command)

    private_key_str = os.environ.get("SSH_PRIVATE_KEY")
    if not private_key_str:
        logger.error("SSH_PRIVATE_KEY environment variable not set.")
        return -3, None, "SSH_PRIVATE_KEY environment variable not set."

    try:
        # Load private key from string
        # Add passphrase=None if your key is not passphrase protected
        client_key = asyncssh.import_private_key(private_key_str)
    except (asyncssh.KeyImportError, ValueError) as e:
        logger.exception("Failed to import private key")
        return -3, None, f"Failed to import private key: {e}"

    # Get host details from loaded config using attribute access
    try:
        target_details = config.settings.host_details.get(host_alias)  # Use .get() on the dict first
        if not target_details:
            raise KeyError(f"Host alias '{host_alias}' not found in host_details.")

        # Now use attribute access on the HostConnectionDetails object
        target_host = target_details.hostname
        target_user = target_details.user
        jump_alias = target_details.jump_host_alias  # Access optional attribute directly
    except KeyError:  # Catch key error from the initial dict lookup
        logger.exception("Host alias '%s' not found in configuration.", host_alias)
        return -2, None, f"Host alias '{host_alias}' not found in configuration."
    except Exception as e:
        logger.exception("Error accessing configuration for alias '%s'", host_alias)
        return -2, None, f"Error accessing configuration for alias '{host_alias}': {e}"

    conn = None
    jump_conn = None
    try:
        if jump_alias:
            # --- Connect through Jump Host ---
            try:
                # Use .get() and attribute access for jump host details too
                jump_details = config.settings.host_details.get(jump_alias)
                if not jump_details:
                    raise KeyError(f"Jump host alias '{jump_alias}' not found in host_details.")
                jump_host = jump_details.hostname
                jump_user = jump_details.user
            except KeyError:
                logger.exception("Jump host alias '%s' not found in configuration.", jump_alias)
                return -2, None, f"Jump host alias '{jump_alias}' not found in configuration."
            except Exception as e:
                logger.exception("Error accessing configuration for jump alias '%s'", jump_alias)
                return -2, None, f"Error accessing configuration for jump alias '{jump_alias}': {e}"

            logger.info(
                "Connecting via jump host '%s' (%s@%s) to target '%s' (%s@%s)",
                jump_alias,
                jump_user,
                jump_host,
                host_alias,
                target_user,
                target_host,
            )

            # Establish connection to the jump host first
            jump_conn = await asyncssh.connect(
                jump_host, username=jump_user, client_keys=[client_key], options=CONNECTION_OPTIONS
            )
            logger.info("Connected to jump host '%s'.", jump_alias)

            # Establish tunneled connection to the target host
            conn = await jump_conn.connect_ssh(
                target_host, username=target_user, client_keys=[client_key], options=CONNECTION_OPTIONS
            )
            logger.info("Tunneled connection established to target '%s'.", host_alias)

        else:
            # --- Direct Connection ---
            logger.info("Connecting directly to '%s' (%s@%s)", host_alias, target_user, target_host)
            conn = await asyncssh.connect(
                target_host, username=target_user, client_keys=[client_key], options=CONNECTION_OPTIONS
            )
            logger.info("Direct connection established to '%s'.", host_alias)

        # --- Execute Command ---
        logger.info("Executing command on '%s': %s", host_alias, command)
        # Add a timeout for the command execution itself
        result = await asyncio.wait_for(conn.run(command, check=False), timeout=30)  # 30 second timeout for command
        logger.info("Command finished on '%s' with exit code %s", host_alias, result.exit_status)
        if result.stderr:
            logger.warning("SSH stderr on '%s': %s", host_alias, result.stderr.strip())

        return result.exit_status, result.stdout, result.stderr

    except asyncssh.Error as e:
        logger.exception("SSH connection or command error for alias '%s': %s", host_alias, e)
        return -1, None, f"SSH Error: {e}"
    except TimeoutError:
        logger.exception("SSH command or connection timed out for alias '%s'", host_alias)
        return -4, None, "SSH command or connection timed out"
    except Exception as e:
        logger.exception("An unexpected error occurred for alias '%s': %s", host_alias, e)
        return -5, None, f"Unexpected Error: {e}"
    finally:
        # Ensure connections are closed
        if conn:
            conn.close()
            await conn.wait_closed()
            logger.debug("Target connection to '%s' closed.", host_alias)
        if jump_conn:
            jump_conn.close()
            await jump_conn.wait_closed()
            logger.debug("Jump host connection to '%s' closed.", jump_alias)
