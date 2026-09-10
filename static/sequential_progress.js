(() => {
  const panel = document.getElementById('sequential-progress');
  const actions = document.getElementById('sequential-actions');
  const form = document.querySelector('.sequential-discovery-form');
  if (!panel || !form) return;

  const message = document.getElementById('sequential-progress-message');
  const percent = document.getElementById('sequential-progress-percent');
  const fill = document.getElementById('sequential-progress-fill');
  const errorBox = document.getElementById('sequential-progress-error');
  const track = panel.querySelector('.progress-track');

  function render(status) {
    const value = Math.max(0, Math.min(100, Number(status.percent || 0)));
    panel.hidden = false;
    message.textContent = status.message || 'Working...';
    percent.textContent = `${Math.round(value)}%`;
    fill.style.width = `${value}%`;
    track.setAttribute('aria-valuenow', String(Math.round(value)));
    if (status.error) {
      errorBox.hidden = false;
      errorBox.textContent = status.error;
      actions.hidden = false;
    }
  }

  async function poll() {
    const response = await fetch(panel.dataset.statusUrl, {cache: 'no-store'});
    const status = await response.json();
    render(status);
    if (status.complete && status.review_url) {
      window.location.href = status.review_url;
      return;
    }
    if (status.running) setTimeout(poll, 700);
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    actions.hidden = true;
    errorBox.hidden = true;
    panel.hidden = false;
    const response = await fetch(panel.dataset.startUrl, {method: 'POST'});
    const status = await response.json();
    render(status);
    setTimeout(poll, 400);
  });
})();
