(() => {
  const progressPanel = document.getElementById("discovery-progress");
  if (!progressPanel) return;

  const startUrl = progressPanel.dataset.startUrl;
  const statusUrl = progressPanel.dataset.statusUrl;
  const actions = document.getElementById("discovery-actions");
  const message = document.getElementById("discovery-progress-message");
  const percent = document.getElementById("discovery-progress-percent");
  const fill = document.getElementById("discovery-progress-fill");
  const errorBox = document.getElementById("discovery-progress-error");
  const progressTrack = progressPanel.querySelector("[role='progressbar']");
  let polling = false;

  function renderStatus(status) {
    const value = Math.max(0, Math.min(100, Number(status.percent || 0)));
    progressPanel.hidden = false;
    message.textContent = status.message || "Scanning fields...";
    percent.textContent = `${Math.round(value)}%`;
    fill.style.width = `${value}%`;
    progressTrack.setAttribute("aria-valuenow", String(Math.round(value)));

    if (status.error) {
      errorBox.textContent = status.error;
      errorBox.hidden = false;
      if (actions) actions.hidden = false;
    } else {
      errorBox.hidden = true;
    }

    if (status.complete && status.review_url) {
      window.location.assign(status.review_url);
    }
  }

  async function readStatus() {
    const response = await fetch(statusUrl, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not read field scan progress.");
    return response.json();
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      while (true) {
        const status = await readStatus();
        renderStatus(status);
        if (!status.running) break;
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      if (actions) actions.hidden = false;
    } finally {
      polling = false;
    }
  }

  document.querySelectorAll(".discovery-form").forEach(form => {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (actions) actions.hidden = true;
      progressPanel.hidden = false;
      errorBox.hidden = true;

      try {
        const formData = new FormData(form);
        const response = await fetch(startUrl, { method: "POST", body: formData });
        if (!response.ok) throw new Error("Could not start field scan.");
        renderStatus(await response.json());
        poll();
      } catch (error) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
        if (actions) actions.hidden = false;
      }
    });
  });

  readStatus().then(status => {
    if (status.running) {
      if (actions) actions.hidden = true;
      renderStatus(status);
      poll();
    }
  }).catch(() => {});
})();
