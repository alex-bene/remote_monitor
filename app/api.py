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
        self.client_activity_event = asyncio.Event()

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
        self.client_activity_event.set()

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
            jump_host = config.settings.jump_host
            jump_host_status: models.HostStatus | None = None
            monitored_hosts_status = []
            monitored_hosts_config = config.settings.monitored_hosts

            # 1. Check Jump Host if configured
            if jump_host:
                logger.info("Checking jump host alias: %s", jump_host)
                # Create a dummy MonitoredHostConfig for the jump host check
                # Note: hostname in MonitoredHostConfig is used as the alias here
                jump_host_config = config.MonitoredHostConfig(alias=jump_host, check_gpu=False)
                jump_host_status = await check_host_concurrently(jump_host_config)
            else:
                logger.info("No jump host alias configured, skipping jump host check.")
                jump_host_status = None  # Explicitly set to None

            # 2. Check monitored hosts
            # Proceed if jump host is up OR if no jump host is configured
            if jump_host_status is None or jump_host_status.status == "up":
                if jump_host_status:
                    logger.info("Jump host is up. Checking monitored hosts.")
                else:
                    logger.info("No jump host configured. Checking monitored hosts directly.")

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
                    logger.info("No monitored hosts configured.")
            # 3. Handle case where jump host is configured but down
            elif jump_host and jump_host_status and jump_host_status.status != "up":
                logger.warning("Jump host %s is down or has errors. Skipping monitored hosts.", jump_host)
                error_msg = f"{jump_host} down"  # Use actual jump host name
                monitored_hosts_status = [
                    models.HostStatus(hostname=host_config.alias, status="skipped", error_message=error_msg)
                    for host_config in monitored_hosts_config
                ]
            # Should not happen, but safety net
            else:
                logger.error("Unexpected state in periodic_status_fetch logic.")
                monitored_hosts_status = []

            # Construct response data including jump host status
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
            logger.exception("Error in periodic status fetch task's data retrieval/broadcast")

        try:
            # Determine sleep interval and wait mode
            if status_cache.connected_clients:
                sleep_interval = config.settings.refresh_interval_clients_sec
                is_long_wait = False
                log_msg = "Clients connected, sleeping for %d seconds (K)"  # Not interruptible by event
            else:
                sleep_interval = config.settings.refresh_interval_no_clients_sec
                is_long_wait = True
                log_msg = "No clients connected, sleeping for up to %d seconds (N)"  # Interruptible by event
            logger.info(log_msg, sleep_interval)

            # Clear the event *before* waiting/sleeping
            status_cache.client_activity_event.clear()

            try:
                if is_long_wait:
                    # Long wait: sleep, but allow interruption by client activity event
                    logger.debug("Entering long wait (interruptible)...")
                    await asyncio.wait_for(status_cache.client_activity_event.wait(), timeout=sleep_interval)
                    # If wait_for completes without TimeoutError, the event was set.
                    logger.info("Client activity detected during long wait, interrupting sleep.")
                else:
                    # Short wait: sleep for the fixed duration, ignore event for interruption
                    logger.debug("Entering short wait (non-interruptible)...")
                    await asyncio.sleep(sleep_interval)
                    logger.debug("Short sleep interval of %d seconds completed.", sleep_interval)

            except TimeoutError:
                # This only happens if wait_for timed out during a long wait
                logger.debug("Long sleep interval of %d seconds completed without client activity.", sleep_interval)
            # Note: No specific handling needed here if asyncio.sleep completes in the 'else' block.

        except Exception:
            logger.exception("Error during sleep/wait logic in periodic status fetch")
            # Fallback sleep to prevent tight loop on unexpected error in sleep logic
            await asyncio.sleep(60)  # Sleep briefly before next loop iteration


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


# This function now directly calls the async metrics function
# It expects a MonitoredHostConfig object directly
async def check_host_concurrently(host_config: config.MonitoredHostConfig) -> models.HostStatus:
    """Wrapper to call the async get_full_host_status and handle potential exceptions."""
    host_alias = host_config.alias  # Use the correct attribute 'alias'
    try:
        # Directly await the now asynchronous function from metrics.py
        status = await metrics.get_full_host_status(host_config)
        return status
    except Exception as e:
        # Log the exception if the task itself fails unexpectedly
        logger.exception("Error running check_host_concurrently for alias %s", host_alias)
        # Return an error status
        return models.HostStatus(hostname=host_alias, status="error", error_message=f"Task execution failed: {e}")


@router.get("/api/status", response_model=models.ApiResponse)
async def get_status() -> models.ApiResponse:
    """Get the status of all hosts."""
    logger.info("Received request for /api/status")

    jump_host = config.settings.jump_host
    jump_host_status: models.HostStatus | None = None
    monitored_hosts_status = []
    monitored_hosts_config = config.settings.monitored_hosts

    # 1. Check Jump Host if configured
    if jump_host:
        logger.info("Checking jump host alias: %s", jump_host)
        # Create a dummy MonitoredHostConfig for the jump host check
        jump_host_config = config.MonitoredHostConfig(alias=jump_host, check_gpu=False)
        jump_host_status = await check_host_concurrently(jump_host_config)
    else:
        logger.info("No jump host alias configured, skipping jump host check.")
        jump_host_status = None  # Explicitly set to None

    # 2. Check monitored hosts
    # Proceed if jump host is up OR if no jump host is configured
    if jump_host_status is None or jump_host_status.status == "up":
        if jump_host_status:
            logger.info("Jump host is up. Checking monitored hosts: %s", [h.alias for h in monitored_hosts_config])
        else:
            logger.info(
                "No jump host configured. Checking monitored hosts directly: %s",
                [h.alias for h in monitored_hosts_config],
            )

        if monitored_hosts_config:
            # Create tasks for checking each monitored host
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
    # 3. Handle case where jump host is configured but down
    elif jump_host and jump_host_status and jump_host_status.status != "up":
        logger.warning("Jump host %s is down or has errors. Skipping monitored hosts.", jump_host)
        error_msg = f"{jump_host} down"  # Use actual jump host name
        monitored_hosts_status = [
            models.HostStatus(hostname=host_config.alias, status="skipped", error_message=error_msg)
            for host_config in monitored_hosts_config
        ]
    # Should not happen, but safety net
    else:
        logger.error("Unexpected state in get_status logic.")
        monitored_hosts_status = []

    # Construct response data including jump host status
    response_data = models.ApiResponse(jump_host_status=jump_host_status, monitored_hosts_status=monitored_hosts_status)
    logger.info("Finished processing /api/status request.")

    return response_data
