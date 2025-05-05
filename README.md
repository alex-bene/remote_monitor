# Remote Monitor

Remote Monitor is a web application that monitors the status and metrics of remote machines via SSH. It provides a real-time view of your infrastructure through a simple web interface.

## Features

- Monitor jump host status.
- Monitor status (up/down) and metrics (CPU Usage, RAM Usage/Total, GPU Utilization/Memory/Temp/Power) of configured hosts.
- Supports connecting to monitored hosts directly or via a specified jump host.
- Real-time updates using Server-Sent Events (SSE).
- Configurable data fetching intervals (different intervals when clients are connected vs. not connected).

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

3.  **Configure Monitored Machines:**

    Create or update the `config.yaml` file in the root directory. This file defines connection details and which hosts to monitor.

    **Example `config.yaml`:**

    ```yaml
    page_title: "My Lab Monitor" # Optional: Title shown on the web page

    # Optional: Define an alias for a jump host if needed
    jump_host: "my-jump-box-alias"

    # Define connection details for all hosts (jump and monitored)
    # Keys are aliases used elsewhere in the config.
    host_details:
      my-jump-box-alias: # Details for the jump host
        hostname: "jump.example.com" # Actual hostname or IP
        user: "jumpuser"
        # jump_host_alias: null # Jump host connects directly

      gpu-server-1: # Details for a monitored host
        hostname: "192.168.1.101"
        user: "gpuadmin"
        jump_host_alias: "my-jump-box-alias" # Connect via the jump host

      cpu-node-5: # Details for another monitored host
        hostname: "cpu5.internal.net"
        user: "nodeuser"
        jump_host_alias: "my-jump-box-alias" # Connect via the jump host

    # List the hosts you want to actively monitor on the dashboard
    monitored_hosts:
      - alias: "gpu-server-1" # Must match a key in host_details
        check_gpu: true # Check GPU metrics for this host

      - alias: "cpu-node-5" # Must match a key in host_details
        check_gpu: false # Don't check GPU metrics

    # Optional: Refresh intervals (defaults shown)
    refresh_interval_no_clients_sec: 1800 # Default: 30 minutes
    refresh_interval_clients_sec: 300 # Default: 5 minutes
    ```

4.  **Set Up SSH Authentication:**

    - The application uses an SSH private key to authenticate.
    - Provide your **unencrypted** private key (e.g., the contents of `~/.ssh/id_rsa`) via the `SSH_PRIVATE_KEY` environment variable.
    - **Security Note:** Ensure this environment variable is handled securely (e.g., using a `.env` file listed in `.gitignore`, or system-level secrets management). Do not commit the key to version control.
    - Create a `.env` file in the project root:
      ```dotenv
      # .env
      SSH_PRIVATE_KEY="-----BEGIN OPENSSH PRIVATE KEY-----
      bdawhuslllawudDAWLHu3293hdfADA938dh99d3h3wwewwwwwwwwwwwwwdadawd3DDDDD...
      ...your private key content...
      -----END OPENSSH PRIVATE KEY-----"
      ```
    - Ensure the public key corresponding to this private key is added to the `~/.ssh/authorized_keys` file on the **jump host** (if used) and all **target monitored hosts** for the specified users.

5.  **Run the Application:**

    ```bash
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    ```

    The application will be available at `http://127.0.0.1:8000`.

6.  **Deployment:**

    For deployment to a server, use a `gunicord`

    ```bash
    gunicorn -k uvicorn.workers.UvicornWorker app.main:app
    ```

## Configuration (`config.yaml`)

- `page_title` (Optional): Sets the title displayed in the web browser tab. Defaults to "Remote Monitor".
- `jump_host` (Optional): The **alias** (must be a key in `host_details`) of the host to use as a jump/bastion server. If omitted, hosts are connected to directly.
- `host_details`: A dictionary where keys are unique **aliases** for your hosts (both jump and monitored). Each value is an object containing:
  - `hostname`: The actual hostname or IP address of the machine.
  - `user`: The username to use for the SSH connection.
  - `jump_host_alias` (Optional): The **alias** of the jump host to use for connecting to _this_ specific host. If omitted or `null`, a direct connection is attempted.
- `monitored_hosts`: A list of hosts to actively monitor and display on the dashboard. Each item is an object containing:
  - `alias`: The **alias** of the host (must match a key in `host_details`).
  - `check_gpu` (Optional): Boolean indicating whether to fetch GPU metrics (`nvidia-smi`). Defaults to `true`. Set to `false` if the host has no GPUs or you don't want to monitor them.
- `refresh_interval_no_clients_sec` (Optional): Interval (seconds) for fetching data when no web clients are connected. Defaults to 1800 (30 minutes). Minimum 600.
- `refresh_interval_clients_sec` (Optional): Interval (seconds) for fetching data when at least one web client is connected. Defaults to 300 (5 minutes). Minimum 60.

## Technologies Used

- FastAPI: Web framework
- uvicorn: ASGI server
- uv: Dependency management
- asyncssh: Asynchronous SSH client library
- Jinja2: Templating engine
- SSE (Server-Sent Events): For real-time updates
- PyYAML: For configuration file parsing
- Pydantic: For data validation and settings management
- python-dotenv: For loading environment variables (like `SSH_PRIVATE_KEY`)
- asyncio: For asynchronous operations
- HTML, CSS, JavaScript: For the frontend

## Contributing

This project was mostly written by Gemini, an AI model, with occasional "guidance" (read: scolding when it did something spectacularly stupid) from the human user. Cline, a VS Code extension, was used for this ... collaboration.
