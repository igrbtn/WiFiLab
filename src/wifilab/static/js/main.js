// App shell: navigation, hash router, status line and the Location Services banner.
import {h, api, $, ago, toast} from "./lib.js";
import {livePage, channelsPage, findPage, historyPage, settingsPage} from "./live.js";
import {surveysPage, surveyPage} from "./survey.js";
import {reportPage} from "./report.js";

const NAV = [["#/live", "Live"], ["#/channels", "Channels"], ["#/history", "History"], ["#/surveys", "Surveys"],
  ["#/report/live", "Report"], ["#/settings", "Settings"]];

let cleanup = null;
let info = null;

function nav() {
  const cur = location.hash || "#/live";
  return h("nav", NAV.map(([href, label]) => h("a" + (cur.startsWith(href.split("/").slice(0, 2).join("/")) ? ".on" : ""), {href}, label)));
}

export function locationBanner(loc) {
  if (!loc || !loc.needed || loc.authorized) {
    if (loc && loc.hidden_results) {
      return h("div.notice.warn", `${loc.hidden_results} network(s) came back without SSID/BSSID. Allow Location Services for WiFiLab.`);
    }
    return null;
  }
  const btn = h("button.b.small", {type: "button", onclick: async () => {
    const r = await api("/api/location/request", {method: "POST"});
    toast(r.message || `Location: ${r.status}`, r.authorized ? "ok" : "info", 10000);
  }}, "Request permission");
  const why = loc.status === "denied" || loc.status === "restricted"
    ? "Location Services access is denied for this app. Turn it on in System Settings > Privacy & Security > Location Services (WiFiLab), then rescan."
    : !loc.services_enabled ? "Location Services are turned off in System Settings > Privacy & Security."
      : loc.bundled ? "WiFiLab has not been allowed Location Services yet."
        : "Running from a terminal: the permission belongs to the app that started Python. Start /Applications/WiFiLab.app (scripts/build_app.sh) to get SSIDs and BSSIDs.";
  return h("div.notice.warn", h("b", "macOS hides network names and BSSIDs without Location Services. "), why,
    loc.hidden_results ? ` (${loc.hidden_results} hidden in the last scan)` : "", " ", btn);
}

async function statusLine() {
  try {
    info = await api("/api/info");
    const st = info.state;
    const el = $("#status");
    const state = st.error_code ? h("span.err", st.error) : h("span", st.running ? `scanning every ${st.interval} s` : "paused");
    el.replaceChildren(h("span.muted", `WiFiLab ${info.version} | ${info.backend} | `), state,
      h("span.muted", ` | last scan ${ago(st.last_ts)}, ${st.ap_count} BSSIDs`));
    const b = $("#banner");
    b.replaceChildren(...[locationBanner(info.location)].filter(Boolean));
  } catch (e) {
    $("#status").replaceChildren(h("span.err", e.message));
  }
}

async function route() {
  if (cleanup) { try { cleanup(); } catch { /* page already gone */ } cleanup = null; }
  const hash = location.hash || "#/live";
  const parts = hash.slice(2).split("/").map(decodeURIComponent);
  const main = $("#main");
  $("#nav").replaceChildren(nav());
  main.replaceChildren(h("div.empty", "Loading..."));
  const pages = {
    live: () => livePage(), channels: () => channelsPage(), history: () => historyPage(), settings: () => settingsPage(),
    find: () => findPage(parts[1]), surveys: () => surveysPage(), survey: () => surveyPage(parts[1], parts[2]),
    report: () => reportPage(parts[1] || "live"),
  };
  const fn = pages[parts[0]] || pages.live;
  try {
    const page = await fn();
    main.replaceChildren(page.el);
    cleanup = page.cleanup || null;
    if (page.mounted) page.mounted();
  } catch (e) {
    main.replaceChildren(h("div.notice.err", e.message));
  }
}

window.addEventListener("hashchange", route);
route();
statusLine();
setInterval(statusLine, 5000);
