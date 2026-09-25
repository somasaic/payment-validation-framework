// Mock storefront — shared behaviour for store, cart and checkout pages.
// Cart state lives in localStorage per merchant. Nothing is ever submitted.

const merchantId = location.pathname.split("/")[2];
const api = (path) => fetch(`/api/v1/merchants/${merchantId}${path}`).then((r) => {
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
});

const cartKey = `cart:${merchantId}`;
const readCart = () => JSON.parse(localStorage.getItem(cartKey) || "[]");
const writeCart = (items) => {
  localStorage.setItem(cartKey, JSON.stringify(items));
  renderCartCount();
};

let currency = "USD";
const money = (amount) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);

function renderCartCount() {
  const count = readCart().reduce((n, i) => n + i.qty, 0);
  document.querySelector("[data-testid=cart-count]").textContent = count;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

function paymentIcon(method, selectable) {
  const img = el("img", { src: method.icon_url, alt: method.display_name, width: 38, height: 24 });
  const name = el("span", { class: "method-name", text: method.display_name });
  const item = el("li", {
    class: `payment-icon payment-icon--${method.code.replace("_", "-")}`,
    "data-payment-method": method.code,
    "data-method-type": method.type,
  });
  if (selectable) {
    const radio = el("input", {
      type: "radio", name: "payment-method", value: method.code, "aria-label": method.display_name,
    });
    item.append(el("label", {}, radio, img, name));
  } else {
    item.append(img);
  }
  return item;
}

// ── Header + footer (all pages) ─────────────────────────────────────────────

async function initChrome() {
  const merchant = await api("");
  currency = merchant.currency;
  document.title = `${merchant.merchant_name} — ${document.title}`;
  document.querySelector("[data-testid=store-name]").textContent = merchant.merchant_name;
  renderCartCount();

  const footer = await api("/payment-methods?context=footer");
  const list = document.querySelector("[data-testid=footer-payment-icons]");
  list.replaceChildren(...footer.methods.map((m) => paymentIcon(m, false)));
}

// ── Store page ──────────────────────────────────────────────────────────────

async function initStore() {
  const catalog = await api("/products");
  const grid = document.querySelector("[data-testid=product-grid]");
  const toast = document.querySelector("[data-testid=toast]");

  grid.replaceChildren(...catalog.items.map((p) =>
    el("article", { class: "product-card", "data-testid": "product-card", "data-sku": p.sku },
      el("h3", { text: p.name }),
      el("p", { class: "price", "data-testid": "product-price", text: money(p.price) }),
      el("button", {
        type: "button",
        text: "Add to cart",
        onclick: () => {
          const items = readCart();
          const existing = items.find((i) => i.sku === p.sku);
          if (existing) existing.qty += 1;
          else items.push({ sku: p.sku, name: p.name, price: p.price, qty: 1 });
          writeCart(items);
          toast.textContent = `${p.name} added to cart`;
        },
      }),
    ),
  ));
}

// ── Cart page ───────────────────────────────────────────────────────────────

function initCart() {
  const body = document.querySelector("[data-testid=cart-items]");
  const empty = document.querySelector("[data-testid=cart-empty]");
  const subtotal = document.querySelector("[data-testid=cart-subtotal]");
  const checkout = document.querySelector("[data-testid=checkout-button]");

  const setQty = (sku, qty) => {
    const items = readCart()
      .map((i) => (i.sku === sku ? { ...i, qty } : i))
      .filter((i) => i.qty > 0);
    writeCart(items);
    render();
  };

  function render() {
    const items = readCart();
    body.replaceChildren(...items.map((i) =>
      el("tr", { "data-testid": "cart-item", "data-sku": i.sku },
        el("td", { class: "item-name", text: i.name }),
        el("td", {},
          el("button", { type: "button", "aria-label": `Decrease quantity of ${i.name}`, text: "−", onclick: () => setQty(i.sku, i.qty - 1) }),
          el("output", { "data-testid": "item-qty", text: i.qty }),
          el("button", { type: "button", "aria-label": `Increase quantity of ${i.name}`, text: "+", onclick: () => setQty(i.sku, i.qty + 1) }),
        ),
        el("td", { "data-testid": "item-total", text: money(i.price * i.qty) }),
        el("td", {}, el("button", { type: "button", text: "Remove", onclick: () => setQty(i.sku, 0) })),
      ),
    ));
    const total = items.reduce((n, i) => n + i.price * i.qty, 0);
    subtotal.textContent = money(total);
    empty.hidden = items.length > 0;
    checkout.disabled = items.length === 0;
  }

  checkout.addEventListener("click", () => { location.href = "checkout"; });
  render();
}

// ── Checkout page ───────────────────────────────────────────────────────────

function initCheckout() {
  const items = readCart();
  const summary = document.querySelector("[data-testid=order-summary]");
  const total = items.reduce((n, i) => n + i.price * i.qty, 0);
  summary.replaceChildren(
    ...(items.length
      ? items.map((i) => el("li", { "data-testid": "summary-item", text: `${i.qty} × ${i.name}` }))
      : [el("li", { "data-testid": "summary-empty", text: "Your cart is empty" })]),
  );
  document.querySelector("[data-testid=order-total]").textContent = money(total);

  const panel = document.querySelector("[data-testid=payment-methods]");
  const loading = document.querySelector("[data-testid=payment-loading]");
  const error = document.querySelector("[data-testid=payment-error]");
  const list = document.querySelector("[data-testid=payment-method-list]");
  const selected = document.querySelector("[data-testid=selected-payment-method]");
  const email = document.querySelector("#email");
  const cont = document.querySelector("[data-testid=continue-button]");
  const review = document.querySelector("[data-testid=order-review]");

  const update = () => {
    const choice = list.querySelector("input:checked");
    selected.textContent = choice
      ? `Selected: ${choice.closest("li").querySelector(".method-name").textContent}`
      : "Selected: none";
    cont.disabled = !(choice && email.checkValidity() && email.value && items.length);
  };

  async function loadPaymentMethods() {
    panel.setAttribute("aria-busy", "true");
    loading.hidden = false;
    error.hidden = true;
    list.hidden = true;
    const latency = new URLSearchParams(location.search).get("payment_latency_ms");
    try {
      const data = await api(`/payment-methods?context=checkout${latency ? `&latency_ms=${latency}` : ""}`);
      list.replaceChildren(...data.methods.map((m) => paymentIcon(m, true)));
      list.hidden = false;
    } catch {
      error.hidden = false;
    } finally {
      loading.hidden = true;
      panel.setAttribute("aria-busy", "false");
      update();
    }
  }

  list.addEventListener("change", update);
  email.addEventListener("input", update);
  document.querySelector("[data-testid=payment-retry]").addEventListener("click", loadPaymentMethods);
  cont.addEventListener("click", () => {
    review.hidden = false;
    review.querySelector("[data-testid=review-method]").textContent =
      selected.textContent.replace("Selected: ", "");
  });

  loadPaymentMethods();
}

// ── Boot ────────────────────────────────────────────────────────────────────

const pageInit = { store: initStore, cart: initCart, checkout: initCheckout };
initChrome()
  .then(() => pageInit[document.body.dataset.page]())
  .finally(() => { document.body.dataset.ready = "true"; });
