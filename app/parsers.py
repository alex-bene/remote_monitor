import logging
import re
from typing import Any

from .models import GpuInfo, HostMetrics, ProcessInfo

logger = logging.getLogger(__name__)


def parse_cpu_usage(top_output: str) -> float | None:
    """Parse CPU usage percentage from `top -bn1` output. Handles both . and , as decimal separators."""
    # Example line (period): %Cpu(s):  1.5 us,  0.5 sy,  0.0 ni, 97.9 id,  0.1 wa,  0.0 hi,  0.0 si,  0.0 st
    # Example line (comma):  %Cpu(s):  0,0 ut,  0,0 sy,  0,0 ni,100,0 id,  0,0 wa, ...
    # Regex looks for digits, then either a comma or period, then more digits, before " id"
    match = re.search(r"%Cpu\(s\):\s*.*?(\d+[,.]\d+)\s+id", top_output)
    if match:
        try:
            # Replace comma with period for float conversion
            idle_str = match.group(1).replace(",", ".")
            idle_percentage = float(idle_str)
            # Calculate usage as 100 - idle
            usage_percentage = round(100.0 - idle_percentage, 1)
        except (ValueError, IndexError):
            logger.warning("Could not parse CPU idle percentage from: %s", match.group(1))
        else:
            return usage_percentage
    else:
        logger.warning("Could not find CPU usage line in top output: %s...", top_output[:200])  # Log snippet
    return None


def parse_memory_usage_from_top(top_output: str) -> dict[str, Any] | None:
    """Parse memory usage from `top -bn1` output (MiB Mem line)."""
    # Example line: MiB Mem :  63958.8 total,  15063.5 free,   1995.2 used,  47641.3 buff/cache
    # Regex looks for "MiB Mem :", then captures total, free, used, buff/cache (handling . or , decimals)
    match = re.search(
        r"MiB Mem\s*:\s*(\d+[,.]\d+)\s+total,\s*(\d+[,.]\d+)\s+free,\s*(\d+[,.]\d+)\s+used,\s*(\d+[,.]\d+)\s+buff/cache",
        top_output,
    )
    if match:
        try:
            total_mib = float(match.group(1).replace(",", "."))
            used_mib = float(match.group(3).replace(",", "."))  # Capture 'used'
            # Calculate percentage based on used/total
            usage_percent = round(used_mib / total_mib * 100.0, 1) if total_mib > 0 else 0.0

            return {
                "ram_total_mb": int(total_mib),  # Convert to int MB
                "ram_used_mb": int(used_mib),  # Convert to int MB
                "ram_usage_percent": usage_percent,
            }
        except (ValueError, IndexError):
            logger.exception("Could not parse memory values from top output line: %s", match.group(0))
    else:
        logger.warning("Could not find Mem line in top output: %s...", top_output[:200])  # Log snippet
    return None


def parse_host_metrics(top_output: str | None) -> HostMetrics | None:
    """Combine CPU and RAM parsing from top output into a HostMetrics object."""
    if not top_output:
        return None

    cpu_usage = parse_cpu_usage(top_output)
    mem_info = parse_memory_usage_from_top(top_output)

    if cpu_usage is None and mem_info is None:
        return None  # Nothing could be parsed

    metrics_data = {}
    if cpu_usage is not None:
        metrics_data["cpu_usage_percent"] = cpu_usage
    if mem_info is not None:
        metrics_data.update(mem_info)

    return HostMetrics(**metrics_data)


def parse_nvidia_smi_csv(csv_output: str, expected_keys: list[str], warn_on_empty: bool = True) -> list[dict[str, Any]]:
    """Parse the CSV output of `nvidia-smi ... --format=csv,...`."""
    items = []
    lines = csv_output.strip().splitlines()

    if not lines:
        if warn_on_empty:
            logger.warning("Received empty nvidia-smi GPU query output.")
        return items

    num_expected_keys = len(expected_keys)

    for i, line in enumerate(lines):
        values = [v.strip() for v in line.split(",")]
        if len(values) != num_expected_keys:
            logger.warning(
                "Skipping malformed nvidia-smi GPU line %d: %s. Expected %d values, got %d",
                i + 1,
                line,
                num_expected_keys,
                len(values),
            )
            continue
        try:
            item_data = {}
            for key, value in zip(expected_keys, values, strict=True):
                # Handle specific type conversions
                if key in ["power.limit", "power.draw"]:
                    item_data[key] = float(value)
                elif key in ["name", "process_name"]:  # Handle GPU name and process name as strings
                    item_data[key] = value
                else:  # Assume int for others (index, pid, utilization, memory, temp, used_gpu_memory)
                    item_data[key] = int(value)
            items.append(item_data)
        except (ValueError, KeyError, IndexError, TypeError):
            logger.exception("Error parsing nvidia-smi GPU line %d: %s.", i + 1, line)
    return items


def parse_gpu_info(
    gpu_query_output: str | None, per_gpu_process_output: dict[int, str | None] | None
) -> list[GpuInfo] | None:
    """Combine GPU and Process parsing into a list of GpuInfo objects, using per-GPU process data."""
    if not gpu_query_output:
        # Let's return None to indicate GPU info wasn't available/parsed.
        return None

    gpu_list_data = parse_nvidia_smi_csv(
        csv_output=gpu_query_output,
        expected_keys=[
            "index",
            "name",
            "utilization.gpu",
            "memory.used",
            "memory.total",
            "temperature.gpu",
            "power.limit",
            "power.draw",
        ],  # Added new keys
        warn_on_empty=True,
    )
    if not gpu_list_data:
        return None  # Parsing failed or no GPUs found

    gpu_infos = []
    for gpu_data in gpu_list_data:
        processes = []
        current_gpu_index = gpu_data.get("index")

        if current_gpu_index is not None and per_gpu_process_output:
            # Get the specific process output string for this GPU index
            specific_process_output = per_gpu_process_output.get(current_gpu_index)

            if specific_process_output is not None:  # Check if query was successful (even if empty)
                # Parse the process data *only* for this GPU
                process_list_data_for_gpu = parse_nvidia_smi_csv(
                    csv_output=specific_process_output,
                    expected_keys=["pid", "process_name", "used_gpu_memory"],  # Original keys
                    warn_on_empty=False,  # Don't warn if a specific GPU has no processes
                )
                # Create ProcessInfo objects from the parsed data for this GPU
                try:
                    processes = [ProcessInfo(**proc_data) for proc_data in process_list_data_for_gpu]
                except Exception:
                    logger.exception("Error creating ProcessInfo objects for GPU %s", current_gpu_index)
            else:
                logger.warning(
                    "Process query failed or was skipped for GPU %s, processes will be empty.", current_gpu_index
                )

        try:
            # Create GpuInfo object, Pydantic handles alias mapping
            gpu_info = GpuInfo(**gpu_data, processes=processes)
            gpu_infos.append(gpu_info)
        except Exception:  # Catch potential Pydantic validation errors too
            logger.exception("Error creating GpuInfo object for GPU %s", gpu_data.get("index", "N/A"))

    return gpu_infos if gpu_infos else None


if __name__ == "__main__":
    # --- Test CPU/RAM Parsing ---
    print("\n--- Testing CPU/RAM Parsing ---")
    test_top_output = """
top - 09:30:00 up 10 days, 1:15,  1 user,  load average: 0.05, 0.10, 0.15
Tasks: 200 total,   1 running, 199 sleeping,   0 stopped,   0 zombie
%Cpu(s):  1.2 us,  0.8 sy,  0.0 ni, 97.5 id,  0.2 wa,  0.0 hi,  0.3 si,  0.0 st
MiB Mem :  64348.0 total,  45678.0 free,  12345.0 used,   6325.0 buff/cache
MiB Swap:   2047.0 total,   2047.0 free,      0.0 used.  51234.0 avail Mem
    """
    test_free_output = """
               total        used        free      shared  buff/cache   available
Mem:           64348       12345       45678         123        6325       51234
Swap:           2047           0        2047
    """
    host_metrics = parse_host_metrics(test_top_output)
    print(f"Parsed Host Metrics: {host_metrics}")

    # --- Test GPU Parsing ---
    print("\n--- Testing GPU Parsing ---")
    test_gpu_query = """
0, NVIDIA GeForce RTX 3090, 15, 2048, 24576
1, NVIDIA GeForce RTX 3090, 5, 1024, 24576
"""
    test_process_query = """
1234, python, 1024
5678, /usr/bin/torchrun, 8192
"""
    gpu_info_list = parse_gpu_info(test_gpu_query, test_process_query)
    print(f"Parsed GPU Info: {gpu_info_list}")

    test_gpu_query_malformed = """
0, NVIDIA GeForce RTX 3090, 15, 2048
1, NVIDIA GeForce RTX 3090, 5, 1024, 24576, extra
"""
    test_process_query_malformed = """
1234, python
5678, /usr/bin/torchrun, 8192, extra
"""
    print("\n--- Testing Malformed GPU Parsing ---")
    gpu_info_list_malformed = parse_gpu_info(test_gpu_query_malformed, test_process_query_malformed)
    print(f"Parsed Malformed GPU Info: {gpu_info_list_malformed}")  # Should log warnings

    print("\n--- Testing Empty GPU Process Parsing ---")
    gpu_info_list_no_proc = parse_gpu_info(test_gpu_query, "")
    print(f"Parsed GPU Info (No Processes): {gpu_info_list_no_proc}")
