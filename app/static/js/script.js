document.addEventListener("DOMContentLoaded", () => {
  const API_ENDPOINT = "/api/status_sse";
  const MIB_TO_GIB = 1024; // Conversion factor

  // DOM Elements
  const lastUpdatedElem = document.getElementById("last-updated");
  const jumpHostCard = document.getElementById("jump-host-status");
  const monitoredHostsTableBody = document.querySelector("#monitored-hosts-table tbody");

  // Templates
  const hostRowTemplate = document.getElementById("host-row-template");
  const gpuDetailTemplate = document.getElementById("gpu-detail-template");
  const gpuProcessTemplate = document.getElementById("gpu-process-template");

  // Store expanded rows state
  const expandedRows = new Set();

  function updateTimestamp() {
    const now = new Date();
    lastUpdatedElem.textContent = `Last updated: ${now.toLocaleTimeString()}`;
  }

  function clearContainer(container) {
    container.innerHTML = ""; // Clear previous content
  }

  function getStatusIndicatorHtml(status) {
    let emoji = "⚪"; // Default checking
    switch (status) {
      case "up":
        emoji = "🟢";
        break;
      case "down":
        emoji = "🔴";
        break;
      case "error":
        emoji = "🟠";
        break;
      case "skipped":
        emoji = "⚫";
        break;
    }
    // Use a span with a class for styling the background color circle
    return `<span class="status-indicator status-${status}"></span>${emoji}`;
  }

  function updateJumpHostCard(hostData) {
    jumpHostCard.querySelector(".hostname").textContent = hostData.hostname;
    // For the jump host card, we still use the background color class
    jumpHostCard.classList.remove("status-up", "status-down", "status-error", "status-checking", "status-skipped");
    jumpHostCard.classList.add(`status-${hostData.status}`);

    const indicator = jumpHostCard.querySelector(".status-indicator");
    if (indicator) {
      indicator.innerHTML = getStatusIndicatorHtml(hostData.status);
    }

    const errorElem = jumpHostCard.querySelector(".error-message");
    errorElem.textContent = hostData.error_message || "";
    errorElem.style.display = hostData.error_message ? "block" : "none";

    // Jump host card doesn't display metrics/gpus in this design, so no need to update those sections
  }

  function createMonitoredHostRow(hostData) {
    const row = hostRowTemplate.content.cloneNode(true).querySelector(".host-row");
    const gpuDetailsRow = row.nextElementSibling; // The next row is the details row

    row.dataset.hostname = hostData.hostname; // Store hostname for toggling
    gpuDetailsRow.dataset.hostname = hostData.hostname; // Store hostname for toggling

    // Update main row cells
    row.querySelector(".status-cell").innerHTML = getStatusIndicatorHtml(hostData.status);
    row.querySelector(".hostname-cell").textContent = hostData.hostname;

    // CPU Usage
    if (hostData.metrics && hostData.metrics.cpu_usage_percent !== null) {
      row.querySelector(".cpu-cell").textContent = `${hostData.metrics.cpu_usage_percent}%`;
    } else {
      row.querySelector(".cpu-cell").textContent = "N/A";
    }

    // Memory Usage (in GiB)
    if (hostData.metrics && hostData.metrics.ram_used_mb !== null && hostData.metrics.ram_total_mb !== null) {
      const used_gib = (hostData.metrics.ram_used_mb / MIB_TO_GIB).toFixed(1);
      const total_gib = (hostData.metrics.ram_total_mb / MIB_TO_GIB).toFixed(1);
      row.querySelector(".memory-cell").textContent = `${used_gib} / ${total_gib} GiB`;
    } else {
      row.querySelector(".memory-cell").textContent = "N/A";
    }

    // GPU Count
    if (hostData.gpus && hostData.gpus.length > 0) {
      row.querySelector(".gpu-count-cell").textContent = hostData.gpus.length;
      // Make row clickable if it has GPUs
      row.classList.add("has-gpus");
      row.addEventListener("click", () => toggleGpuDetails(hostData.hostname, hostData.gpus));
    } else {
      row.querySelector(".gpu-count-cell").textContent = "0";
    }

    // Errors
    const errorCell = row.querySelector(".error-cell");
    if (hostData.error_message && hostData.error_message.includes("unreachable")) {
      errorCell.textContent = "unreachable";
    } else {
      errorCell.textContent = hostData.error_message || "";
    }

    return [row, gpuDetailsRow]; // Return both rows
  }

  function toggleGpuDetails(hostname, gpus) {
    const detailsRow = document.querySelector(`.gpu-details-row[data-hostname="${hostname}"]`);
    const contentDiv = detailsRow.querySelector(".gpu-details-content");

    if (expandedRows.has(hostname)) {
      // Hide details
      detailsRow.classList.add("hidden");
      expandedRows.delete(hostname);
      clearContainer(contentDiv); // Clear content when hidden
    } else {
      // Show details
      detailsRow.classList.remove("hidden");
      expandedRows.add(hostname);

      // Populate GPU details content
      clearContainer(contentDiv); // Clear previous content
      if (gpus && gpus.length > 0) {
        gpus.forEach((gpu) => {
          const gpuDetailCard = gpuDetailTemplate.content.cloneNode(true).querySelector(".gpu-detail-card");
          gpuDetailCard.querySelector(".gpu-index").textContent = gpu.index;
          gpuDetailCard.querySelector(".gpu-name").textContent = gpu.name;
          gpuDetailCard.querySelector(".gpu-util").textContent = gpu.utilization_gpu_percent;
          // Convert GPU memory to GiB for display
          gpuDetailCard.querySelector(".gpu-mem-used").textContent = (gpu.memory_used_mib / MIB_TO_GIB).toFixed(1);
          gpuDetailCard.querySelector(".gpu-mem-total").textContent = (gpu.memory_total_mib / MIB_TO_GIB).toFixed(1);

          const processList = gpuDetailCard.querySelector(".gpu-processes ul");
          clearContainer(processList); // Clear previous processes in template clone

          if (gpu.processes && gpu.processes.length > 0) {
            gpu.processes.forEach((proc) => {
              const processItem = gpuProcessTemplate.content.cloneNode(true).querySelector("li");
              processItem.querySelector(".proc-pid").textContent = proc.pid;
              processItem.querySelector(".proc-cmd").textContent = proc.command;
              processItem.querySelector(".proc-mem").textContent = proc.used_gpu_memory_mib; // Keep process memory in MiB
              processList.appendChild(processItem);
            });
          } else {
            processList.innerHTML = "<li>No processes running on this GPU.</li>";
          }
          contentDiv.appendChild(gpuDetailCard);
        });
      } else {
        contentDiv.innerHTML = "<p>No GPU information available.</p>";
      }
    }
  }

  function updateStatus(data, timestamp) {
    console.log("Status data received:", data);
    console.log("Update timestamp received:", timestamp); // Log the timestamp

    // Update Jump Host
    updateJumpHostCard(data.jump_host_status);

    // Update Monitored Hosts Table
    clearContainer(monitoredHostsTableBody); // Clear previous rows
    if (data.monitored_hosts_status && data.monitored_hosts_status.length > 0) {
      data.monitored_hosts_status.forEach((hostData) => {
        const [hostRow, gpuDetailsRow] = createMonitoredHostRow(hostData);
        monitoredHostsTableBody.appendChild(hostRow);
        monitoredHostsTableBody.appendChild(gpuDetailsRow);

        // If this row was previously expanded, re-expand it and populate
        if (expandedRows.has(hostData.hostname) && hostData.gpus && hostData.gpus.length > 0) {
          toggleGpuDetails(hostData.hostname, hostData.gpus); // This will re-show and populate
        }
      });
    } else if (data.jump_host_status.status === "up") {
      monitoredHostsTableBody.innerHTML = '<tr><td colspan="6">No monitored hosts configured or found.</td></tr>';
    } else {
      monitoredHostsTableBody.innerHTML = '<tr><td colspan="6">Jump host is down, skipping monitored hosts.</td></tr>';
    }

    // Update timestamp using the provided timestamp
    if (timestamp) {
      try {
        const date = new Date(timestamp);
        // Format the date nicely, similar to updateTimestamp
        lastUpdatedElem.textContent = `Last updated: ${date.toLocaleTimeString()}`;
      } catch (error) {
        console.error("Error parsing timestamp:", error);
        lastUpdatedElem.textContent = `Last updated: Invalid Date`;
      }
    } else {
      // Fallback to current time if timestamp is not available
      updateTimestamp();
    }
  }

  function connectToSSE() {
    const eventSource = new EventSource(API_ENDPOINT);

    eventSource.onmessage = (event) => {
      try {
        let jsonData = event.data;
        // Check if the data starts with the SSE "data: " prefix and remove it if present
        if (jsonData.startsWith("data: ")) {
          jsonData = jsonData.substring(6); // Remove "data: "
        }
        const responseData = JSON.parse(jsonData);
        console.log("SSE data received:", responseData); // Log the raw received data

        // Extract the actual status data from the nested 'data' key
        if (responseData && responseData.data) {
          updateStatus(responseData.data, responseData.last_updated); // Pass timestamp
        } else {
          console.error("SSE data received but missing expected 'data' key:", responseData);
        }
      } catch (error) {
        console.error("Error parsing SSE data:", error);
      }
    };

    eventSource.onerror = (error) => {
      console.error("SSE error:", error);
      // Handle errors (e.g., reconnecting)
    };
  }

  // On initial load, connect to SSE
  connectToSSE();
});
