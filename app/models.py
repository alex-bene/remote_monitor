from pydantic import BaseModel, Field


class ProcessInfo(BaseModel):
    """Information about a process running on a GPU."""

    pid: int
    command: str = Field(..., alias="process_name")  # Alias to match nvidia-smi output key
    used_gpu_memory_mib: int = Field(..., alias="used_gpu_memory")  # Alias


class GpuInfo(BaseModel):
    """Detailed information about a single GPU."""

    index: int
    name: str
    utilization_gpu_percent: int = Field(..., alias="utilization.gpu")  # Alias
    memory_used_mib: int = Field(..., alias="memory.used")  # Alias
    memory_total_mib: int = Field(..., alias="memory.total")  # Alias
    temperature_gpu: int = Field(..., alias="temperature.gpu")  # Added
    power_limit: float = Field(..., alias="power.limit")  # Added - nvidia-smi outputs float like 250.00 W
    power_draw: float = Field(..., alias="power.draw")  # Added - nvidia-smi outputs float like 55.50 W
    processes: list[ProcessInfo] = []


class HostMetrics(BaseModel):
    """Basic CPU and RAM metrics for a host."""

    cpu_usage_percent: float | None = None
    ram_usage_percent: float | None = None
    ram_total_mb: int | None = None
    ram_used_mb: int | None = None


class HostStatus(BaseModel):
    """Overall status and metrics for a single monitored host."""

    hostname: str
    status: str  # e.g., "up", "down", "error", "checking"
    error_message: str | None = None
    metrics: HostMetrics | None = None
    gpus: list[GpuInfo] | None = None


class ApiResponse(BaseModel):
    """Structure of the main API response."""

    jump_host_status: HostStatus | None = None
    monitored_hosts_status: list[HostStatus]
