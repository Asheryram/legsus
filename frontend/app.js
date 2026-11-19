/* LegSus frontend — vanilla JS + Leaflet, no build step. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ghs = (n) => "GH¢" + Number(n).toFixed(2);
  const mins = (m) => (m >= 60 ? `${Math.floor(m / 60)}h ${Math.round(m % 60)}m` : `${Math.round(m)} min`);
  const dist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);

  const state = { from: null, to: null, plan: null, selected: 0, writable: true };

  // ---------- map ----------
  const map = L.map("map", { zoomControl: false }).setView([5.6037, -0.187], 12);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors",
  }).addTo(map);
  const routeLayer = L.layerGroup().addTo(map);
  const pin = (color) => L.divIcon({
    className: "", iconSize: [18, 18], iconAnchor: [9, 9],
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>`,
  });
  const markers = { from: L.marker([0, 0], { icon: pin("#137a52"), draggable: true }),
                    to: L.marker([0, 0], { icon: pin("#c8372d"), draggable: true }) };
  for (const k of ["from", "to"]) {
    markers[k].on("dragend", (e) => {
      const ll = e.target.getLatLng();
      setPoint(k, { lat: ll.lat, lon: ll.lng, name: "Dropped pin" });
      if (state.from && state.to) plan();
    });
  }

  function setPoint(which, p) {
    state[which] = p;
    $(which + "Input").value = p.name;
    markers[which].setLatLng([p.lat, p.lon]).addTo(map);
  }

  map.on("click", (e) => {
    const which = document.querySelector('input[name="pick"]:checked').value;
    setPoint(which, { lat: e.latlng.lat, lon: e.latlng.lng, name: `Pin ${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)}` });
    if (which === "from") document.querySelector('input[name="pick"][value="to"]').checked = true;
    if (state.from && state.to) plan();
  });

  // ---------- search / autocomplete ----------
  async function searchPlaces(q) {
    const local = await fetch(`/api/search?q=${encodeURIComponent(q)}`).then((r) => r.json()).catch(() => []);
    if (local.length >= 4) return local;
    // Fallback: OpenStreetMap Nominatim, limited to Ghana (called from YOUR browser).
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=gh&limit=5&q=${encodeURIComponent(q)}`;
      const osm = await fetch(url, { headers: { "Accept-Language": "en" } }).then((r) => r.json());
      return local.concat(osm.map((o) => ({ name: o.display_name.split(",").slice(0, 2).join(","), lat: +o.lat, lon: +o.lon, kind: "place" })));
    } catch { return local; }
  }

  function wireAutocomplete(which) {
    const input = $(which + "Input");
    const list = $(which + "Suggest");
    let timer, items = [], active = -1;
    const close = () => { list.classList.remove("open"); active = -1; };
    const choose = (it) => { setPoint(which, it); close(); map.panTo([it.lat, it.lon]); if (state.from && state.to) plan(); };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) return close();
      timer = setTimeout(async () => {
        items = await searchPlaces(q);
        list.innerHTML = items.map((it, i) => `<li data-i="${i}"><span>${esc(it.name)}</span><span class="kind">${esc(it.kind)}</span></li>`).join("");
        list.classList.toggle("open", items.length > 0);
      }, 220);
    });
    list.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li");
      if (li) choose(items[+li.dataset.i]);
    });
    input.addEventListener("keydown", (e) => {
      if (!list.classList.contains("open")) return;
      const lis = list.querySelectorAll("li");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + lis.length) % lis.length;
        lis.forEach((l, i) => l.classList.toggle("active", i === active));
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault(); choose(items[active]);
      } else if (e.key === "Escape") close();
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
  }
  wireAutocomplete("from");
  wireAutocomplete("to");

  $("locBtn").addEventListener("click", () => {
    if (!navigator.geolocation) return status("Location not available on this device.");
    status("Getting your location…");
    navigator.geolocation.getCurrentPosition(
      (pos) => { setPoint("from", { lat: pos.coords.latitude, lon: pos.coords.longitude, name: "My location" });
                 map.setView([pos.coords.latitude, pos.coords.longitude], 15); status(""); if (state.to) plan(); },
      () => status("Couldn't get location — tap the map instead."),
      { enableHighAccuracy: true, timeout: 10000 });
  });
  $("swapBtn").addEventListener("click", () => {
    const f = state.from, t = state.to;
    if (t) setPoint("from", t); if (f) setPoint("to", f);
    if (state.from && state.to) plan();
  });
  $("planForm").addEventListener("submit", (e) => { e.preventDefault(); plan(); });

  // ---------- planning ----------
  function status(msg) { $("status").textContent = msg; }

  async function plan() {
    if (!state.from || !state.to) return status("Pick both a start and a destination (search, or tap the map).");
    status("Finding trotros…");
    const q = new URLSearchParams({
      from_lat: state.from.lat, from_lon: state.from.lon, to_lat: state.to.lat, to_lon: state.to.lon,
      from_name: state.from.name, to_name: state.to.name,
    });
    try {
      const r = await fetch(`/api/plan?${q}`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Request failed");
      state.plan = data; state.selected = 0;
      render();
    } catch (err) {
      status("Error: " + err.message);
    }
  }

  function render() {
    const { itineraries, uber, message } = state.plan;
    status(itineraries.length ? `${itineraries.length} option${itineraries.length > 1 ? "s" : ""}` : (message || "No route found."));
    const u = $("uber");
    u.classList.remove("hidden");
    u.innerHTML = `<span>Uber estimate · ${uber.km} km · ~${mins(uber.minutes)}</span><b>${ghs(uber.fare_ghs)}</b>`;
    $("results").innerHTML = itineraries.map((it, i) => card(it, i)).join("");
    document.querySelectorAll(".card-head").forEach((h) =>
      h.addEventListener("click", () => { state.selected = +h.parentElement.dataset.i; render(); }));
    document.querySelectorAll(".fare-row button").forEach((b) => b.addEventListener("click", logFare));
    draw(itineraries[state.selected]);
  }

  function card(it, i) {
    const chips = it.legs.map((l) => l.mode === "walk"
      ? `<span class="chip walk">🚶 ${dist(l.metres)}</span>`
      : `<span class="chip tro">${esc(l.route_ref)}${l.also_routes.length ? " +" + l.also_routes.length : ""}</span>`)
      .join('<span class="arrow">›</span>');
    return `
      <article class="card ${i === state.selected ? "selected" : ""}" data-i="${i}">
        <div class="card-head">
          <div class="t">${mins(it.minutes)}</div>
          <div class="f">${it.fare_is_estimate ? "~" : ""}${ghs(it.fare_ghs)}</div>
          <div class="meta">${it.transfers ? it.transfers + " change" + (it.transfers > 1 ? "s" : "") : "Direct"} · ${dist(it.walk_metres)} walking</div>
          <div class="save">${it.saves_vs_uber_ghs > 0 ? "saves " + ghs(it.saves_vs_uber_ghs) : ""}</div>
          <div class="chips">${chips}</div>
        </div>
        <div class="steps">${it.legs.map(step).join("")}</div>
      </article>`;
  }

  function step(l) {
    if (l.mode === "walk") {
      return `<div class="step walk"><div class="ic">🚶</div><div>
        <h4>Walk ${dist(l.metres)} (~${mins(l.minutes)})</h4>
        <p>to <b>${esc(l.to.name)}</b></p>
        ${l.to.note ? `<p class="note">${esc(l.to.note)}</p>` : ""}
      </div></div>`;
    }
    const also = l.also_routes.length ? `<p>Any of these also work: <b>${l.also_routes.map(esc).join(", ")}</b></p>` : "";
    return `<div class="step tro"><div class="ic">${esc(l.route_ref)}</div><div>
      <h4>Board at ${esc(l.from.name)}</h4>
      <p>Look for the trotro going to <span class="shout">${esc(l.towards)}</span> — mate calls “${esc(l.shout)}”.</p>
      ${also}
      ${l.from.note ? `<p class="note">${esc(l.from.note)}</p>` : ""}
      ${l.route_note ? `<p class="note">${esc(l.route_note)}</p>` : ""}
      <p>Ride ${l.stops} stop${l.stops > 1 ? "s" : ""} · ${dist(l.metres)} · ~${mins(l.minutes)} (+~${mins(l.wait_minutes)} loading)</p>
      <p>Get down at <b>${esc(l.to.name)}</b> — tell the mate “bus stop” before it.</p>
      <details class="stoplist"><summary>Stops on the way</summary><ol>${l.stop_names.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>
      <div class="fare-row">
        <b>${l.fare_source === "estimate" ? "~" : ""}${ghs(l.fare_ghs)}</b><span class="est">${esc(l.fare_source)}</span>
        ${state.writable ? `<input type="number" step="0.1" min="1" placeholder="paid" aria-label="Fare paid" />
        <button data-route="${esc(l.route_ref)}" data-from="${esc(l.from.stop_id)}" data-to="${esc(l.to.stop_id)}">Log fare</button>` : ""}
      </div>
      <p class="sid">stops ${esc(l.from.stop_id)} → ${esc(l.to.stop_id)}</p>
    </div></div>`;
  }

  async function logFare(e) {
    const b = e.currentTarget;
    const val = parseFloat(b.previousElementSibling.value);
    if (!(val > 0)) { b.previousElementSibling.focus(); return; }
    b.disabled = true; b.textContent = "Saving…";
    const r = await fetch("/api/fares", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route: b.dataset.route, from_stop: b.dataset.from, to_stop: b.dataset.to, fare: val }),
    });
    b.textContent = r.ok ? "Saved ✓" : "Failed";
    if (r.ok) setTimeout(plan, 500);
  }

  function draw(it) {
    routeLayer.clearLayers();
    if (!it) return;
    const all = [];
    for (const l of it.legs) {
      const style = l.mode === "walk"
        ? { color: "#6b7280", weight: 4, dashArray: "4 8" }
        : { color: "#0b3d2e", weight: 6, opacity: 0.9 };
      L.polyline(l.geometry, style).addTo(routeLayer);
      if (l.mode === "trotro") {
        L.circleMarker([l.from.lat, l.from.lon], { radius: 7, color: "#0b3d2e", fillColor: "#f2b705", fillOpacity: 1, weight: 3 })
          .bindTooltip(`Board ${l.route_ref} @ ${l.from.name}`).addTo(routeLayer);
        L.circleMarker([l.to.lat, l.to.lon], { radius: 6, color: "#0b3d2e", fillColor: "#fff", fillOpacity: 1, weight: 3 })
          .bindTooltip(`Get down @ ${l.to.name}`).addTo(routeLayer);
      }
      all.push(...l.geometry);
    }
    if (all.length) map.fitBounds(all, { padding: [30, 30] });
  }

  // saved places as quick picks
  fetch("/api/meta").then((r) => r.json()).then((m) => {
    state.writable = m.writable !== false;
    status(`${m.routes} trotro lines · ${m.stops} stops loaded`);
  }).catch(() => status("API not reachable"));
})();
