const form = document.getElementById("searchForm");
const targetInput = document.getElementById("targetInput");
const limitInput = document.getElementById("limitInput");
const resultsList = document.getElementById("resultsList");
const resultsTitle = document.getElementById("resultsTitle");
const statusBadge = document.getElementById("statusBadge");
const helperText = document.getElementById("helperText");
const clockLabel = document.getElementById("clockLabel");
const providerButtons = Array.from(document.querySelectorAll(".provider-pill"));
const autocompleteList = document.getElementById("autocompleteList");

let provider = "oebb";
let autocompleteTimer = null;

function updateClock() {
  const now = new Date();
  clockLabel.textContent = now.toLocaleString("de-AT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function hideAutocomplete() {
  autocompleteList.hidden = true;
  autocompleteList.innerHTML = "";
}

function renderAutocomplete(items) {
  autocompleteList.innerHTML = "";
  if (!items.length) {
    hideAutocomplete();
    return;
  }

  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "autocomplete-item";
    button.textContent = item.label;
    button.addEventListener("click", () => {
      targetInput.value = item.value;
      hideAutocomplete();
    });
    autocompleteList.appendChild(button);
  });

  autocompleteList.hidden = false;
}

async function fetchAutocomplete() {
  if (provider !== "oebb") {
    hideAutocomplete();
    return;
  }

  const query = targetInput.value.trim();
  if (query.length < 2) {
    hideAutocomplete();
    return;
  }

  try {
    const response = await fetch(
      `/api/autocomplete?provider=${encodeURIComponent(provider)}&query=${encodeURIComponent(query)}`
    );
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Autocomplete failed.");
    }
    renderAutocomplete(data.suggestions || []);
  } catch {
    hideAutocomplete();
  }
}

function scheduleAutocomplete() {
  clearTimeout(autocompleteTimer);
  autocompleteTimer = setTimeout(fetchAutocomplete, 180);
}

function setProvider(nextProvider) {
  provider = nextProvider;
  providerButtons.forEach((button) => {
    button.classList.toggle("provider-pill-active", button.dataset.provider === nextProvider);
  });

  if (provider === "wl") {
    targetInput.placeholder = "e.g. 147";
    helperText.textContent = "For Wiener Linien, enter a numeric stopId. Autocomplete is available for OEBB station names.";
  } else {
    targetInput.placeholder = "e.g. Wien Hbf (Bahnsteige 3-12)";
    helperText.textContent = "Search an OEBB station by name. Suggestions appear while you type.";
  }

  hideAutocomplete();
}

function setLoadingState(isLoading) {
  statusBadge.textContent = isLoading ? "Loading" : "Ready";
  form.querySelector('button[type="submit"]').disabled = isLoading;
}

function renderEmpty(message) {
  resultsList.innerHTML = "";
  const article = document.createElement("article");
  article.className = "empty-state";
  article.innerHTML = `<p>${message}</p>`;
  resultsList.appendChild(article);
}

function stopHtml(stop) {
  const parts = [];
  if (stop.arrival) {
    parts.push(`<span><strong>Arr</strong> ${stop.arrival}</span>`);
  }
  if (stop.departure) {
    parts.push(`<span><strong>Dep</strong> ${stop.departure}</span>`);
  }
  if (stop.platform) {
    parts.push(`<span><strong>Platform</strong> ${stop.platform}</span>`);
  }

  return `
    <li class="stop-row">
      <div class="stop-copy">
        <strong>${stop.name}</strong>
        <div class="stop-meta">${parts.join("")}</div>
      </div>
    </li>
  `;
}

function renderRows(rows) {
  resultsList.innerHTML = "";

  rows.forEach((row, index) => {
    const article = document.createElement("article");
    article.className = "result-card";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-toggle";
    button.setAttribute("aria-expanded", "false");
    button.innerHTML = `
      <div class="time-block">
        <strong>${row.departure}</strong>
        <span>Departure</span>
      </div>
      <div class="time-block">
        <strong>${row.arrival || "--:--"}</strong>
        <span>Arrival</span>
      </div>
      <div>
        <span class="line-pill">${row.line_name}</span>
      </div>
      <div class="location-copy">
        <strong>${row.location}</strong>
        <span>${row.display}</span>
        <em class="expand-hint">${row.stop_details?.length ? "Click to show intermediate stops" : "No stop details available"}</em>
      </div>
    `;

    const details = document.createElement("div");
    details.className = "result-details";
    details.hidden = true;
    const detailStops = row.stop_details || [];
    details.innerHTML = detailStops.length
      ? `
        <div class="detail-head">
          <strong>Intermediate stops</strong>
          <span>${detailStops.length} stops</span>
        </div>
        <ol class="stop-list">
          ${detailStops.map((stop) => stopHtml(stop)).join("")}
        </ol>
      `
      : `
        <div class="detail-head">
          <strong>Intermediate stops</strong>
          <span>No details found</span>
        </div>
      `;

    button.addEventListener("click", () => {
      const isOpen = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!isOpen));
      details.hidden = isOpen;
      article.classList.toggle("result-card-open", !isOpen);
    });

    article.style.animationDelay = `${index * 45}ms`;
    article.appendChild(button);
    article.appendChild(details);
    resultsList.appendChild(article);
  });
}

async function searchDepartures(event) {
  event.preventDefault();

  const payload = {
    provider,
    target: targetInput.value.trim(),
    limit: Number(limitInput.value) || 5,
  };

  if (!payload.target) {
    renderEmpty("Please enter a station name or stopId first.");
    return;
  }

  hideAutocomplete();
  setLoadingState(true);
  statusBadge.textContent = "Searching";
  resultsTitle.textContent = `Searching ${payload.target}`;

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Search failed.");
    }

    resultsTitle.textContent =
      provider === "wl" ? `Wiener Linien stop ${payload.target}` : data.rows[0]?.location || payload.target;
    statusBadge.textContent = `${data.rows.length} results`;

    if (!data.rows.length) {
      renderEmpty("No departures found for that search.");
      return;
    }

    renderRows(data.rows);
  } catch (error) {
    statusBadge.textContent = "Error";
    renderEmpty(error.message);
  } finally {
    setLoadingState(false);
  }
}

providerButtons.forEach((button) => {
  button.addEventListener("click", () => setProvider(button.dataset.provider));
});

targetInput.addEventListener("input", scheduleAutocomplete);
targetInput.addEventListener("focus", scheduleAutocomplete);
targetInput.addEventListener("blur", () => {
  setTimeout(hideAutocomplete, 120);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideAutocomplete();
  }
});

form.addEventListener("submit", searchDepartures);
updateClock();
setInterval(updateClock, 30_000);
setProvider("oebb");
