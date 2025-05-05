import asyncio
import logging

from . import config, models, parsers, ssh_utils

logger = logging.getLogger(__name__)

# --- Constants for Commands ---
CHECK_REACHABILITY_CMD = "exit 0"
CHECK_NVIDIA_SMI_CMD = "command -v nvidia-smi"
TOP_CMD = "top -bn1"
NVIDIA_SMI_GPU_QUERY_CMD = "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.limit,power.draw --format=csv,noheader,nounits"
# Process query command will be constructed per GPU index later


async def check_host_reachability(host_alias: str) -> tuple[bool, str | None]:
    """Check if a host is reachable via async SSH."""
    logger.info("Checking reachability for alias %s...", host_alias)
    # Use the new async SSH command runner
    rc, _, stderr = await ssh_utils.run_ssh_command_async(host_alias, CHECK_REACHABILITY_CMD)
    if rc == 0:
        logger.info("Host alias %s is reachable.", host_alias)
        return True, None
    error_msg = f"Host alias {host_alias} unreachable (SSH rc={rc}). Stderr: {stderr or 'N/A'}"
    logger.warning(error_msg)
    return False, error_msg


async def check_nvidia_smi(host_alias: str) -> tuple[bool, str | None]:
    """Check if nvidia-smi command exists on the remote host via async SSH."""
    logger.info("Checking for nvidia-smi on alias %s...", host_alias)
    # Use the new async SSH command runner
    rc, stdout, stderr = await ssh_utils.run_ssh_command_async(host_alias, CHECK_NVIDIA_SMI_CMD)
    if rc == 0 and stdout and stdout.strip():
        logger.info("nvidia-smi found on alias %s: %s", host_alias, stdout.strip())
        return True, None
    error_msg = f"nvidia-smi not found or check failed on {host_alias} (rc={rc}). Stderr: {stderr or 'N/A'}"
    logger.warning(error_msg)
    # Return False, but no error message *for the status* unless the check itself failed badly
    err_for_status = error_msg if rc != 1 else None  # rc=1 usually means command not found, which is expected
    return False, err_for_status


async def get_host_metrics(host_alias: str) -> tuple[models.HostMetrics | None, str | None]:
    """Fetch and parse CPU and RAM metrics from a host using top output via async SSH."""
    logger.info("Fetching host metrics (CPU/RAM) for alias %s using top...", host_alias)
    error_messages = []
    top_output = None

    # Fetch top output using the new async SSH command runner
    rc_top, stdout_top, stderr_top = await ssh_utils.run_ssh_command_async(host_alias, TOP_CMD)
    if rc_top == 0:
        top_output = stdout_top
    else:
        msg = f"Failed to get top output from {host_alias} (rc={rc_top}). Stderr: {stderr_top or 'N/A'}"
        logger.warning(msg)
        error_messages.append(msg)

    # Try parsing
    metrics = parsers.parse_host_metrics(top_output)
    combined_error = "; ".join(error_messages) if error_messages else None

    if metrics:
        logger.info("Successfully parsed host metrics for %s.", host_alias)
    else:
        logger.warning("Could not parse host metrics for %s. Errors: %s", host_alias, combined_error)

    return metrics, combined_error


async def get_gpu_info(host_alias: str) -> tuple[list[models.GpuInfo] | None, str | None]:
    """Fetch and parse GPU information from a host if nvidia-smi is present via async SSH."""
    logger.info("Fetching GPU info for alias %s...", host_alias)
    # Use await for the async check_nvidia_smi
    has_nvidia_smi, smi_check_error = await check_nvidia_smi(host_alias)

    if smi_check_error:  # Log if the check itself failed unexpectedly
        logger.error("Error during nvidia-smi check on %s: %s", host_alias, smi_check_error)

    if not has_nvidia_smi:
        logger.info("nvidia-smi not found on %s, skipping GPU query.", host_alias)
        # Return None for GPUs, and the check error only if it was significant
        return None, smi_check_error

    error_messages = []
    gpu_query_output = None
    per_gpu_process_output: dict[int, str | None] = {}  # Store process output per GPU index

    # 1. Fetch base GPU query output (indices, names, etc.) using async SSH
    rc_gpu, stdout_gpu, stderr_gpu = await ssh_utils.run_ssh_command_async(host_alias, NVIDIA_SMI_GPU_QUERY_CMD)
    if rc_gpu == 0:
        gpu_query_output = stdout_gpu
    else:
        msg = f"Failed to get nvidia-smi GPU query from {host_alias} (rc={rc_gpu}). Stderr: {stderr_gpu or 'N/A'}"
        logger.warning(msg)
        error_messages.append(msg)
        # If we can't get the basic GPU list, we can't proceed
        return None, "; ".join(error_messages)

    # 2. Parse GPU indices from the base query output
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
    # Ensure index is converted to int for dictionary keys
    gpu_indices = [int(gpu["index"]) for gpu in gpu_list_for_indices if gpu.get("index") is not None]

    if not gpu_indices:
        logger.info("No GPU indices found or parsed for %s. Assuming no GPUs or parse error.", host_alias)
    else:
        logger.info("Found GPU indices for %s: %s. Querying processes per GPU...", host_alias, gpu_indices)
        # 3. Fetch Process query output *per GPU* concurrently
        process_tasks = {}
        for index in gpu_indices:
            specific_process_cmd = (
                f"nvidia-smi -i {index} --query-compute-apps=pid,process_name,used_gpu_memory "
                "--format=csv,noheader,nounits"
            )
            # Create tasks for concurrent execution
            process_tasks[index] = asyncio.create_task(
                ssh_utils.run_ssh_command_async(host_alias, specific_process_cmd),
                name=f"gpu_proc_{host_alias}_{index}",  # Add name for easier debugging
            )

        # Wait for all process query tasks to complete
        process_results = await asyncio.gather(*process_tasks.values(), return_exceptions=True)

        # Process results
        for i, index in enumerate(gpu_indices):
            result = process_results[i]
            if isinstance(result, Exception):
                msg = f"Exception fetching process info for GPU {index} on {host_alias}: {result}"
                logger.error(msg)
                error_messages.append(msg)
                per_gpu_process_output[index] = None  # Indicate error
            elif isinstance(result, tuple):
                rc_proc, stdout_proc, stderr_proc = result
                if rc_proc == 0:
                    per_gpu_process_output[index] = stdout_proc
                    logger.debug("Successfully got process info for GPU %d on %s.", index, host_alias)
                else:
                    no_proc_msg = "No running processes found"
                    if stderr_proc and no_proc_msg in stderr_proc:
                        logger.info("No running processes found on GPU %d for %s.", index, host_alias)
                        per_gpu_process_output[index] = ""  # Indicate success but no processes
                    else:
                        msg = (
                            f"Failed to get nvidia-smi process query for GPU {index} on {host_alias} "
                            f"(rc={rc_proc}). Stderr: {stderr_proc or 'N/A'}"
                        )
                        logger.warning(msg)
                        error_messages.append(msg)
                        per_gpu_process_output[index] = None  # Indicate error
            else:
                # Should not happen with gather
                logger.error("Unexpected result type from gather for GPU %d process query: %s", index, type(result))
                per_gpu_process_output[index] = None

    # 4. Try parsing with the base GPU output and the per-GPU process dictionary
    gpu_info = parsers.parse_gpu_info(gpu_query_output, per_gpu_process_output)
    combined_error = "; ".join(error_messages) if error_messages else None

    if gpu_info:
        logger.info("Successfully parsed combined GPU info for %s.", host_alias)
    # Only log warning if there wasn't already a significant SMI check error
    elif not smi_check_error:
        logger.warning("Could not parse GPU info for %s. Errors: %s", host_alias, combined_error)

    # Return the smi_check_error if it was significant, otherwise the parsing errors
    final_error = smi_check_error or combined_error
    return gpu_info, final_error


async def get_full_host_status(host_config: config.MonitoredHostConfig) -> models.HostStatus:
    """Get the complete status (reachability, metrics, GPUs) for a host via async SSH."""
    # Use the correct attribute 'alias' from the config object
    host_alias = host_config.alias
    logger.info("Getting full status for host alias: %s", host_alias)
    host_status = models.HostStatus(hostname=host_alias, status="checking")  # Use alias for hostname in status
    all_errors = []

    # 1. Check Reachability (await the async version)
    reachable, reachability_error = await check_host_reachability(host_alias)
    if not reachable:
        host_status.status = "down"
        host_status.error_message = reachability_error or f"Host alias {host_alias} unreachable"
        logger.warning("Full status check failed for %s: Unreachable.", host_alias)
        return host_status
    host_status.status = "up"  # Mark as up, might add errors later

    # --- Run metrics and GPU checks concurrently ---
    tasks_to_run = {}
    tasks_to_run["metrics"] = asyncio.create_task(get_host_metrics(host_alias), name=f"metrics_{host_alias}")

    if host_config.check_gpu:
        tasks_to_run["gpu"] = asyncio.create_task(get_gpu_info(host_alias), name=f"gpu_{host_alias}")
    else:
        logger.info("GPU check disabled for %s in config.", host_alias)

    # Wait for metrics and GPU (if applicable) tasks to complete
    results = await asyncio.gather(*tasks_to_run.values(), return_exceptions=True)

    # Process results
    metrics_result = results[0]  # Metrics task is always first
    if isinstance(metrics_result, Exception):
        logger.exception("Error fetching host metrics for %s: %s", host_alias, metrics_result)
        all_errors.append(f"Metrics Task Error: {metrics_result}")
    elif isinstance(metrics_result, tuple):
        metrics, metrics_error = metrics_result
        if metrics:
            host_status.metrics = metrics
        if metrics_error:
            all_errors.append(f"Metrics Error: {metrics_error}")
    else:
        logger.error("Unexpected result type for metrics task: %s", type(metrics_result))
        all_errors.append("Metrics Task Error: Unexpected result type")

    if host_config.check_gpu:
        gpu_result = results[1]  # GPU task is second if it ran
        if isinstance(gpu_result, Exception):
            logger.exception("Error fetching GPU info for %s: %s", host_alias, gpu_result)
            all_errors.append(f"GPU Task Error: {gpu_result}")
        elif isinstance(gpu_result, tuple):
            gpu_info, gpu_error = gpu_result
            if gpu_info:
                host_status.gpus = gpu_info
            # Avoid adding "nvidia-smi not found" as a primary error message if it was just not present
            if gpu_error and "nvidia-smi not found" not in gpu_error:
                all_errors.append(f"GPU Error: {gpu_error}")
        else:
            logger.error("Unexpected result type for GPU task: %s", type(gpu_result))
            all_errors.append("GPU Task Error: Unexpected result type")

    # Finalize status
    if all_errors:
        host_status.error_message = "; ".join(all_errors)
        logger.warning("Full status check for %s completed with errors: %s", host_alias, host_status.error_message)
    else:
        logger.info("Full status check for %s completed successfully.", host_alias)

    return host_status
