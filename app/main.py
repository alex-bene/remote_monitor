import asyncio  # Import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    api,  # Import the API router
    config,  # Import config settings
)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# --- Lifespan Event Handler ---
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # --- Startup Logic ---
    logger.info("Application startup: Initializing...")

    # Start the periodic status fetching background task
    # Make sure api.broadcast_task is initialized (e.g., api.broadcast_task = None)
    # in the api module if it's not guaranteed to exist before this assignment.
    api.broadcast_task = asyncio.create_task(api.periodic_status_fetch())
    logger.info("Periodic status fetch task created.")

    try:
        yield  # The application runs while the context manager is active
    finally:
        # --- Shutdown Logic ---
        logger.info("Application shutdown: Cleaning up...")
        # Cancel the periodic status fetching background task
        if api.broadcast_task and not api.broadcast_task.done():
            api.broadcast_task.cancel()
            try:
                # Give the task a chance to process the cancellation
                await api.broadcast_task
            except asyncio.CancelledError:
                logger.info("Periodic status fetch task cancelled successfully.")
            except Exception:
                # Log any other exceptions that might occur during cancellation cleanup
                logger.exception("Error occurred during background task cancellation.")
        else:
            logger.info("Background task was already done or not found.")


# --- App Setup ---
# Use the title from the loaded configuration settings
app = FastAPI(
    title=config.settings.page_title,
    description="Monitors status and metrics of configured hosts via SSH.",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Include API Router ---
app.include_router(api.router)

# --- Static Files Setup ---
# Get the directory of the current file (main.py)
current_dir = Path(__file__).parent
static_dir = current_dir / "static"
templates_dir = current_dir / "templates"

# Create static directory if it doesn't exist (important for mounting)
(static_dir / "css").mkdir(parents=True, exist_ok=True)
(static_dir / "js").mkdir(parents=True, exist_ok=True)

# Create templates directory if it doesn't exist
templates_dir.mkdir(parents=True, exist_ok=True)


# Mount the static directory to serve CSS, JS files
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- Templates Setup ---
templates = Jinja2Templates(directory=templates_dir)


# --- Root Endpoint to Serve Frontend ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request) -> HTMLResponse:
    """Serve the main HTML frontend page."""
    logger.info("Serving root HTML page.")
    # Check if index.html exists before trying to serve it
    index_html_path = templates_dir / "index.html"
    if not index_html_path.exists():
        logger.error("Frontend template file not found: %s", index_html_path)
        # Return a simple error message if template is missing
        return HTMLResponse(
            content="<html><body><h1>Error: Frontend template (index.html) not found.</h1></body></html>",
            status_code=500,
        )

    # Pass page_title from config settings to the template
    return templates.TemplateResponse("index.html", {"request": request, "page_title": config.settings.page_title})


# --- Optional: Run with Uvicorn (for development) ---
# Usually, you run FastAPI apps using: uvicorn app.main:app --reload
# This block allows running `python -m app.main` directly for simple testing.
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Uvicorn server directly for development...")
    # Note: Reloading might not work perfectly when run this way compared to CLI.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
