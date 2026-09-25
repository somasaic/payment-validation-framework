// Store locator — async search against /api/v1/chain/{region}/stores.

const region = location.pathname.split("/")[2];
const form = document.querySelector("[data-testid=store-search]");
const input = document.querySelector("#store-query");
const results = document.querySelector("[data-testid=store-results]");
const status = document.querySelector("[data-testid=results-status]");
const list = document.querySelector("[data-testid=store-result-list]");

function item(store) {
  const li = document.createElement("li");
  li.dataset.testid = "store-result";
  li.dataset.storeId = store.store_id;
  li.innerHTML = `
    <h3></h3>
    <address data-testid="result-address"></address>
    <span class="muted" data-testid="result-distance"></span>
    <a>View store details</a>`;
  li.querySelector("h3").textContent = store.name;
  li.querySelector("address").textContent = store.address;
  li.querySelector("[data-testid=result-distance]").textContent = store.distance;
  li.querySelector("a").href = store.url;
  return li;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const q = input.value.trim();
  if (q.length < 2) {
    status.textContent = "Enter at least 2 characters";
    return;
  }
  results.setAttribute("aria-busy", "true");
  status.textContent = "Searching…";
  list.replaceChildren();
  try {
    const resp = await fetch(`/api/v1/chain/${region}/stores?q=${encodeURIComponent(q)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    list.replaceChildren(...data.items.map(item));
    status.textContent = data.total
      ? `${data.total} store${data.total === 1 ? "" : "s"} found`
      : `No stores found near "${q}"`;
  } catch {
    status.textContent = "Store search is unavailable. Please try again.";
  } finally {
    results.setAttribute("aria-busy", "false");
  }
});
