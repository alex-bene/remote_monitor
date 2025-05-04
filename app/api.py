import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from . import config, metrics, models

logger = logging.getLogger(__name__)
router = APIRouter()


class StatusCache:
    def __init__(self):
        self.latest_status_data: dict | None = None
        self.latest_status_timestamp: str | None = None
        self.connected_clients: set[asyncio.Queue] = set()

    def update_status(self, data: dict, timestamp: str):
        self.latest_status_data = data
        self.latest_status_timestamp = timestamp

    def get_latest_status_message(self) -> str | None:
        if self.latest_status_data is not None:
            initial_message = {"data": self.latest_status_data, "last_updated": self.latest_status_timestamp}
            return f"data: {json.dumps(initial_message)}\n\n"
        return None

    def add_client(self, queue: asyncio.Queue):
        self.connected_clients.add(queue)
        logger.info("Client connected to SSE. Added to set. Total clients: %d", len(self.connected_clients))

    def remove_client(self, queue: asyncio.Queue):
        if queue in self.connected_clients:
            self.connected_clients.remove(queue)
            logger.info(
                "Client disconnected from SSE. Removed from set. Total clients: %d", len(self.connected_clients)
            )

    async def broadcast(self, message: str):
        # Iterate over a copy of the set in case it's modified during iteration
        for client_queue in list(self.connected_clients):
            try:
                await client_queue.put(message)
            except Exception:
                logger.exception("Failed to send message to client queue. Client likely disconnected.")
                # Cleanup happens in the event_publisher's finally block.


status_cache = StatusCache()


async def periodic_status_fetch() -> None:
    """Background task to fetch status periodically and broadcast."""
    logger.info("Starting periodic status fetch task.")

    while True:
        try:
            # 1. Check Jump Host first
            jump_host = config.settings.jump_host
            jump_host_status = await check_host_concurrently(jump_host)

            monitored_hosts_status = []
            # 2. If Jump Host is up, check monitored hosts concurrently
            if jump_host_status.status == "up":
                monitored_hosts_config = config.settings.monitored_hosts
                if monitored_hosts_config:
                    tasks = [check_host_concurrently(host_config) for host_config in monitored_hosts_config]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if isinstance(result, Exception):
                            logger.exception("Error gathering host status")
                        elif isinstance(result, models.HostStatus):
                            monitored_hosts_status.append(result)
                        else:
                            logger.error("Unexpected result type from asyncio.gather: %s", type(result))
            else:
                monitored_hosts_status = [
                    models.HostStatus(hostname=host_config.hostname, status="skipped", error_message="Jump host down")
                    for host_config in config.settings.monitored_hosts
                ]

            response_data = models.ApiResponse(
                jump_host_status=jump_host_status, monitored_hosts_status=monitored_hosts_status
            )

            # Update global latest data
            status_cache.update_status(response_data.dict(), datetime.now().isoformat())

            # Create the wrapped response data including timestamps
            wrapped_response_data = {
                "data": status_cache.latest_status_data,
                "last_updated": status_cache.latest_status_timestamp,
            }
            sse_message = f"data: {json.dumps(wrapped_response_data)}\n\n"

            # Broadcast the message to all connected clients
            await status_cache.broadcast(sse_message)

        except Exception:
            logger.exception("Error in periodic status fetch task")

        # Determine sleep interval based on connected clients
        if status_cache.connected_clients:
            sleep_interval = config.settings.refresh_interval_clients_sec
            logger.info("Clients connected, sleeping for %d seconds (K)", sleep_interval)
        else:
            sleep_interval = config.settings.refresh_interval_no_clients_sec
            logger.info("No clients connected, sleeping for %d seconds (N)", sleep_interval)

        await asyncio.sleep(sleep_interval)


@router.get("/api/status_sse")
async def get_status_sse(_: Request) -> EventSourceResponse:
    """SSE endpoint to stream host status updates."""
    client_queue = asyncio.Queue()
    status_cache.add_client(client_queue)

    # Send initial cached data to the new client's queue if available
    initial_message = status_cache.get_latest_status_message()
    if initial_message:
        await client_queue.put(initial_message)
        logger.info("Sent initial SSE message to client %s", id(client_queue))

    async def event_publisher() -> AsyncGenerator[str, None]:
        # Wait for updates from this client's queue
        try:
            while True:
                # Wait for a message from the periodic fetch task (including the initial message)
                message = await client_queue.get()
                client_queue.task_done()  # Signal that the item has been processed
                yield message  # Yield the SSE formatted message

        except asyncio.CancelledError:
            # This exception is raised when the client disconnects
            logger.info("Client %s cancelled.", id(client_queue))
        except Exception:
            logger.exception("Error in SSE event publisher for client %s", id(client_queue))
        finally:
            # Clean up when the generator exits
            status_cache.remove_client(client_queue)

    return EventSourceResponse(event_publisher(), ping=15)


# Update the type hint to accept either string or MonitoredHostConfig
async def check_host_concurrently(host_identifier: str | config.MonitoredHostConfig) -> models.HostStatus:
    """Run get_full_host_status in an async manner."""
    # Note: get_full_host_status itself is synchronous because it uses
    # subprocess.run(). Running it via asyncio.to_thread allows it
    # not to block the main FastAPI event loop. For true async SSH,
    # libraries like asyncssh would be needed, adding complexity.

    # Determine the hostname and whether to check GPU based on input type
    if isinstance(host_identifier, str):
        hostname = host_identifier
        # Create a dummy config object for the jump host, always check_gpu=False
        host_config_for_metrics = config.MonitoredHostConfig(hostname=hostname, check_gpu=False)
    elif isinstance(host_identifier, config.MonitoredHostConfig):
        hostname = host_identifier.hostname
        host_config_for_metrics = host_identifier
    else:
        # Should not happen with type hints, but good practice
        logger.error("Invalid type passed to check_host_concurrently: %s", type(host_identifier))
        return models.HostStatus(hostname="Unknown", status="error", error_message="Invalid host identifier type")

    try:
        loop = asyncio.get_running_loop()
        # Pass the created/received host_config object to get_full_host_status
        status = await loop.run_in_executor(None, metrics.get_full_host_status, host_config_for_metrics)
    except Exception as e:
        logger.exception("Error running concurrent check for %s", hostname)
        # Return an error status if the task itself fails unexpectedly
        return models.HostStatus(hostname=hostname, status="error", error_message=f"Task execution failed: {e}")
    else:
        return status


@router.get("/api/status", response_model=models.ApiResponse)
async def get_status() -> models.ApiResponse:
    """Get the status of all hosts."""
    logger.info("Received request for /api/status")

    # 1. Check Jump Host first
    jump_host = config.settings.jump_host
    logger.info("Checking jump host: %s", jump_host)
    jump_host_status = await check_host_concurrently(jump_host)  # Use concurrent check

    # 2. If Jump Host is up, check monitored hosts concurrently
    if jump_host_status.status == "down":
        monitored_hosts_status = []
        monitored_hosts_config = config.settings.monitored_hosts
        logger.info("Jump host is up. Checking monitored hosts: %s", [h.hostname for h in monitored_hosts_config])

        if monitored_hosts_config:
            # Create tasks for checking each monitored host, passing the full config object
            tasks = [check_host_concurrently(host_config) for host_config in monitored_hosts_config]
            # Run tasks concurrently and gather results
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    # Log the exception if asyncio.gather caught one
                    logger.exception("Error gathering host status: %s", result)
                elif isinstance(result, models.HostStatus):
                    monitored_hosts_status.append(result)
                else:
                    logger.error("Unexpected result type from asyncio.gather: %s", type(result))
        else:
            logger.info("No monitored hosts configured.")
    else:
        logger.warning("Jump host %s is down or has errors. Skipping monitored hosts.", jump_host)
        # Create 'skipped' status for monitored hosts if jump host is down
        monitored_hosts_status = [
            models.HostStatus(hostname=host_config.hostname, status="skipped", error_message="Passerelle down")
            for host_config in config.settings.monitored_hosts
        ]

    response_data = models.ApiResponse(jump_host_status=jump_host_status, monitored_hosts_status=monitored_hosts_status)
    logger.info("Finished processing /api/status request.")

    return response_data
