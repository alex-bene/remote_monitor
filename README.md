# Remote Monitor

Remote Monitor is a web application that monitors the status and metrics of remote machines via SSH. It provides a real-time view of your infrastructure through a simple web interface.

## Features

- Monitor jump host status.
- Monitor status and metrics (CPU, memory, GPU) of configured hosts via the jump host.
- Real-time updates using Server-Sent Events (SSE).
- Configurable data fetching intervals based on connected clients.

## Setup and Running

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/alex-bene/remote_monitor.git
    cd remote_monitor
    ```

2.  **Install dependencies:**

    It is recommended to use `uv` for dependency management.

    ```bash
    uv sync
    ```

3.  **Configure monitored machines:**

    Create or update the `machines.yaml` file in the root directory. For example:

    ```yaml
    jump_host: your_jump_host_hostname_or_ip
    monitored_hosts:
      - hostname: host1_hostname_or_ip
        check_gpu: true # Set to true to check GPU metrics
      - hostname: host2_hostname_or_ip
        check_gpu: false
    refresh_interval_no_clients_sec: 600 # Optional: Fetch interval when no clients are connected (default: 600 seconds)
    refresh_interval_clients_sec: 60 # Optional: Fetch interval when clients are connected (default: 60 seconds)
    ```

4.  **Ensure SSH access:**

    Make sure you have SSH access configured for the jump host and the monitored hosts from the jump host. Your SSH configuration (`~/.ssh/config`) should be set up correctly.

5.  **Run the application:**

    ```bash
    uvicorn app.main:app --reload
    ```

    The application will be available at `http://127.0.0.1:8000`.

## Configuration

The application is configured via the `machines.yaml` file.

- `jump_host`: The hostname or IP address of the jump host.
- `monitored_hosts`: A list of hosts to monitor. Each host can have:
  - `hostname`: The hostname or IP address of the monitored host.
  - `check_gpu`: A boolean indicating whether to fetch GPU metrics (defaults to `true`).
- `refresh_interval_no_clients_sec`: The interval (in seconds) for fetching data when no clients are connected to the SSE endpoint. Defaults to 600 seconds (10 minutes).
- `refresh_interval_clients_sec`: The interval (in seconds) for fetching data when at least one client is connected to the SSE endpoint. Defaults to 60 seconds (1 minute).

## Technologies Used

- FastAPI: Web framework
- uvicorn: ASGI server
- uv: Dependency management
- Jinja2: Templating engine
- SSE (Server-Sent Events): For real-time updates
- PyYAML: For configuration file parsing
- Pydantic: For data validation
- asyncio: For asynchronous operations
- SSH: For connecting to remote machines
- HTML, CSS, JavaScript: For the frontend

## Contributing

This project was mostly written by Gemini, an AI model, with occasional "guidance" (read: scolding when it did something spectacularly stupid) from the human user. Cline, a VS Code extension, was used for this ... collaboration.
