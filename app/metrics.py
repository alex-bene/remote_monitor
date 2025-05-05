import logging

from . import config, models, parsers, ssh_utils

logger = logging.getLogger(__name__)

# --- Constants for Commands ---
# Using 'exit 0' which is minimal and POSIX compliant
CHECK_REACHABILITY_CMD = "exit 0"
# Check if nvidia-smi exists and is executable
CHECK_NVIDIA_SMI_CMD = "command -v nvidia-smi"
# Linux-specific commands - might need adjustment for other OS
# Using -bn1 for non-interactive mode, 1 iteration
TOP_CMD = "top -bn1"
# NVIDIA SMI commands for GPU and process info (CSV, no header, no units for easier parsing)
NVIDIA_SMI_GPU_QUERY_CMD = "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.limit,power.draw --format=csv,noheader,nounits"
NVIDIA_SMI_PROCESS_QUERY_CMD = (
    "nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader,nounits"
)


def check_host_reachability(hostname: str) -> tuple[bool, str | None]:
    """Check if a host is reachable via SSH."""
    logger.info("Checking reachability for %s...", hostname)
    rc, _, stderr = ssh_utils.run_ssh_command(hostname, CHECK_REACHABILITY_CMD)
    if rc == 0:
        logger.info("Host %s is reachable.", hostname)
        return True, None
    error_msg = f"Host {hostname} unreachable (SSH rc={rc}). Stderr: {stderr or 'N/A'}"
    logger.warning(error_msg)
    return False, error_msg


def check_nvidia_smi(hostname: str) -> tuple[bool, str | None]:
    """Check if nvidia-smi command exists on the remote host."""
    logger.info("Checking for nvidia-smi on %s...", hostname)
    rc, stdout, stderr = ssh_utils.run_ssh_command(hostname, CHECK_NVIDIA_SMI_CMD)
    if rc == 0 and stdout and stdout.strip():
        logger.info("nvidia-smi found on %s: %s", hostname, stdout.strip())
        return True, None
    error_msg = f"nvidia-smi not found or check failed on {hostname} (rc={rc}). Stderr: {stderr or 'N/A'}"
    logger.warning(error_msg)
    # Return False, but no error message *for the status* unless the check itself failed badly
    err_for_status = error_msg if rc != 1 else None  # rc=1 usually means command not found, which is expected
    return False, err_for_status


def get_host_metrics(hostname: str) -> tuple[models.HostMetrics | None, str | None]:
    """Fetch and parse CPU and RAM metrics from a host using top output."""
    logger.info("Fetching host metrics (CPU/RAM) for %s using top...", hostname)
    error_messages = []
    top_output = None

    # Fetch top output
    rc_top, stdout_top, stderr_top = ssh_utils.run_ssh_command(hostname, TOP_CMD)
    if rc_top == 0:
        top_output = stdout_top
    else:
        msg = f"Failed to get top output from {hostname} (rc={rc_top}). Stderr: {stderr_top or 'N/A'}"
        logger.warning(msg)
        error_messages.append(msg)

    # Try parsing
    # Pass only top_output to the updated parse_host_metrics
    metrics = parsers.parse_host_metrics(top_output)
    combined_error = "; ".join(error_messages) if error_messages else None

    if metrics:
        logger.info("Successfully parsed host metrics for %s.", hostname)
    else:
        logger.warning("Could not parse host metrics for %s. Errors: %s", hostname, combined_error)

    return metrics, combined_error


def get_gpu_info(hostname: str) -> tuple[list[models.GpuInfo] | None, str | None]:
    """Fetch and parse GPU information from a host if nvidia-smi is present."""
    logger.info("Fetching GPU info for %s...", hostname)
    has_nvidia_smi, smi_check_error = check_nvidia_smi(hostname)

    if smi_check_error:  # Log if the check itself failed unexpectedly
        logger.error("Error during nvidia-smi check on %s: %s", hostname, smi_check_error)

    if not has_nvidia_smi:
        logger.info("nvidia-smi not found on %s, skipping GPU query.", hostname)
        # Return None for GPUs, and the check error only if it was significant
        return None, smi_check_error

    error_messages = []
    gpu_query_output = None
    per_gpu_process_output: dict[int, str | None] = {}  # Store process output per GPU index

    # 1. Fetch base GPU query output (indices, names, etc.)
    rc_gpu, stdout_gpu, stderr_gpu = ssh_utils.run_ssh_command(hostname, NVIDIA_SMI_GPU_QUERY_CMD)
    if rc_gpu == 0:
        gpu_query_output = stdout_gpu
    else:
        msg = f"Failed to get nvidia-smi GPU query from {hostname} (rc={rc_gpu}). Stderr: {stderr_gpu or 'N/A'}"
        logger.warning(msg)
        error_messages.append(msg)
        # If we can't get the basic GPU list, we can't proceed
        return None, "; ".join(error_messages)

    # 2. Parse GPU indices from the base query output
    # Temporarily parse just to get indices. The full parsing happens later.
    # IMPORTANT: This needs to expect *all* the keys returned by NVIDIA_SMI_GPU_QUERY_CMD now.
    gpu_list_for_indices = parsers.parse_nvidia_smi_csv(
        gpu_query_output,
        [
            "index",
            "name",
            "utilization.gpu",
            "memory.used",
            "memory.total",
            "temperature.gpu",
            "power.limit",
            "power.draw",
        ],
        warn_on_empty=False,
    )
    gpu_indices = [gpu.get("index") for gpu in gpu_list_for_indices if gpu.get("index") is not None]

    if not gpu_indices:
        logger.info("No GPU indices found or parsed for %s. Assuming no GPUs or parse error.", hostname)
        # Still return the base gpu_query_output for parsing later, maybe it finds GPUs without processes
    else:
        logger.info("Found GPU indices for %s: %s. Querying processes per GPU...", hostname, gpu_indices)
        # 3. Fetch Process query output *per GPU*
        for index in gpu_indices:
            # Construct the command for the specific GPU index
            specific_process_cmd = (
                f"nvidia-smi -i {index} --query-compute-apps=pid,process_name,used_gpu_memory "
                "--format=csv,noheader,nounits"
            )
            rc_proc, stdout_proc, stderr_proc = ssh_utils.run_ssh_command(hostname, specific_process_cmd)

            if rc_proc == 0:
                per_gpu_process_output[index] = stdout_proc
                logger.debug("Successfully got process info for GPU %d on %s.", index, hostname)
            else:
                # Check stderr for "No running processes found" which is not a fatal error
                no_proc_msg = "No running processes found"
                if stderr_proc and no_proc_msg in stderr_proc:
                    logger.info("No running processes found on GPU %d for %s.", index, hostname)
                    per_gpu_process_output[index] = ""  # Store empty string to indicate query success but no processes
                else:
                    # Treat other errors as warnings/errors
                    msg = (
                        f"Failed to get nvidia-smi process query for GPU {index} on {hostname} "
                        f"(rc={rc_proc}). Stderr: {stderr_proc or 'N/A'}"
                    )
                    logger.warning(msg)
                    error_messages.append(msg)
                    per_gpu_process_output[index] = None  # Indicate error for this GPU's process query

    # 4. Try parsing with the base GPU output and the per-GPU process dictionary
    gpu_info = parsers.parse_gpu_info(gpu_query_output, per_gpu_process_output)
    combined_error = "; ".join(error_messages) if error_messages else None

    if gpu_info:
        logger.info("Successfully parsed combined GPU info for %s.", hostname)
    else:
        logger.warning("Could not parse GPU info for %s. Errors: %s", hostname, combined_error)

    return gpu_info, combined_error


def get_full_host_status(host_config: config.MonitoredHostConfig) -> models.HostStatus:
    """Get the complete status (reachability, metrics, GPUs) for a host."""
    hostname = host_config.hostname
    logger.info("Getting full status for host: %s", hostname)
    host_status = models.HostStatus(hostname=hostname, status="checking")
    all_errors = []

    # 1. Check Reachability
    reachable, reachability_error = check_host_reachability(hostname)
    if not reachable:
        host_status.status = "down"
        host_status.error_message = reachability_error or "Host unreachable"
        logger.warning("Full status check failed for %s: Unreachable.", hostname)
        return host_status
    host_status.status = "up"  # Mark as up, might add errors later

    # 2. Get Host Metrics (CPU/RAM)
    metrics, metrics_error = get_host_metrics(hostname)
    if metrics:
        host_status.metrics = metrics
    if metrics_error:
        all_errors.append(f"Metrics Error: {metrics_error}")

    # 3. Get GPU Info (Conditional based on config)
    if host_config.check_gpu:
        gpu_info, gpu_error = get_gpu_info(hostname)
        if gpu_info:
            host_status.gpus = gpu_info
        if gpu_error and "nvidia-smi not found" not in gpu_error:
            all_errors.append(f"GPU Error: {gpu_error}")
    else:
        logger.info("GPU check disabled for %s in config.", hostname)

    # Finalize status
    if all_errors:
        host_status.error_message = "; ".join(all_errors)
        logger.warning("Full status check for %s completed with errors: %s", hostname, host_status.error_message)
    else:
        logger.info("Full status check for %s completed successfully.", hostname)

    return host_status
