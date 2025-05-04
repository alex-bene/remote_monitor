import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# SSH options
# Consider making StrictHostKeyChecking and UserKnownHostsFile configurable
# for production environments for better security.
SSH_OPTIONS = [
    "-o",
    "ConnectTimeout=10",  # Increased timeout slightly
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
]


def run_ssh_command(hostname: str, command: str) -> tuple[int, str | None, str | None]:
    """Execute a command on a remote host via SSH.

    Args:
        hostname: The hostname or IP address of the remote machine.
        command: The command string to execute.

    Returns:
        A tuple containing:
        - return_code (int): The exit code of the SSH command. 0 for success.
        - stdout (Optional[str]): The standard output, or None if an error occurred before execution.
        - stderr (Optional[str]): The standard error, or None if an error occurred before execution.

    """
    ssh_command = ["ssh", *SSH_OPTIONS, hostname, "--", command]
    logger.info("Executing SSH command on %s: %s", hostname, " ".join(ssh_command))

    try:
        # Using subprocess.run for simplicity
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            check=False,  # Don't raise exception on non-zero exit code, we handle it
            timeout=15,  # Add a timeout for the subprocess itself
        )
        logger.info("SSH command finished on %s with code %s", hostname, result.returncode)
        # Log stderr if present, especially for non-zero exit codes
        if result.stderr:
            logger.warning("SSH stderr on %s: %s", hostname, result.stderr.strip())
    except subprocess.TimeoutExpired:
        logger.exception("SSH command timed out on %s: %s", hostname, command)
        return -1, None, "SSH command timed out"  # Use a distinct return code for timeout
    except FileNotFoundError:
        logger.exception("SSH command not found. Ensure 'ssh' is in your PATH.")
        # This is a local error, unlikely but possible
        return -2, None, "Local SSH client not found"
    except Exception as e:
        logger.exception("An unexpected error occurred running SSH on %s", hostname)
        return -3, None, f"Unexpected SSH error: {e}"
    else:
        return result.returncode, result.stdout, result.stderr
