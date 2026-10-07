// Channel graph helpers (colours, axis, labels, filters). Run with node by tests/test_js.py.
import assert from "node:assert/strict";

const {SSID_PALETTE, HIDDEN_COLOR, ssidHash, ssidColors, colorOf, ssidKey, axisFor, shape, labelCandidates, placeLabels,
  boxesOverlap, filterAps, sig, CH5} = await import("../../src/wifilab/static/js/spectrum.js");

const names = Array.from({length: 20}, (_, i) => `Net-${i}`);
const c1 = ssidColors(names), c2 = ssidColors([...names].reverse().concat(names, ["", ""]));
for (const n of names) assert.deepEqual(c1.get(n), c2.get(n), n);
const solid = [...c1.values()].filter((c) => !c.dash);
assert.equal(new Set(solid.map((c) => c.i)).size, SSID_PALETTE.length, "first SSIDs get distinct colours");
assert.equal(ssidColors(["Corp"]).get("Corp").i, ssidHash("Corp") % SSID_PALETTE.length);
assert.equal(colorOf(c1, "").color, HIDDEN_COLOR);
assert.equal(ssidKey({ssid: "Y", hidden: true}), "");

assert.deepEqual(axisFor("2.4", []).chans, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
assert.equal(axisFor("2.4", [{channel: 14}]).chans.at(-1), 14);
const ax5 = axisFor("5", [{channel: 149, span_lo: 147, span_hi: 163}]);
assert.ok(ax5.chans.includes(149) && ax5.chans.includes(36) && !ax5.chans.includes(148));
assert.ok(ax5.chans.every((c) => CH5.includes(c)));
assert.ok(axisFor("6", [{channel: 37, span_lo: 31, span_hi: 47}]).chans.every((c) => (c - 1) % 4 === 0));
assert.equal(shape(0), 0); assert.equal(shape(0.5), 1);

const aps = [{bssid: "a", ssid: "X", center: 6, rssi: -50}, {bssid: "b", ssid: "X", center: 6, rssi: -60},
  {bssid: "c", ssid: "Y", center: 6, rssi: -55}, {bssid: "d", ssid: "X", center: 11, rssi: -70}];
assert.deepEqual(labelCandidates(aps).map((a) => a.bssid), ["a", "c", "d"]);
const placed = placeLabels([{id: 1, x: 50, y: 50, w: 40, h: 12}, {id: 2, x: 55, y: 50, w: 40, h: 12}, {id: 3, x: 52, y: 50, w: 40, h: 12}]);
assert.deepEqual(placed.map((p) => p.id), [1, 2]);
assert.ok(!boxesOverlap(placed[0], placed[1]));

const list = [{ssid: "Corp", bssid: "02:00:00:00:00:01", band: "2.4", channel: 1, sec_class: "ent", rssi: -50, vendor: "Aruba"},
  {ssid: "", hidden: true, bssid: "02:00:00:00:00:02", band: "5", channel: 36, sec_class: "wpa2", rssi: -80},
  {ssid: "Cafe", bssid: "02:00:00:00:00:03", band: "2.4", channel: 11, sec_class: "open", rssi_max: -70}];
assert.equal(filterAps(list, {}).length, 3);
assert.deepEqual(filterAps(list, {band: "2.4"}).map((a) => a.ssid), ["Corp", "Cafe"]);
assert.deepEqual(filterAps(list, {sec: "open"}).map((a) => a.ssid), ["Cafe"]);
assert.equal(filterAps(list, {minRssi: -75}).length, 2);
assert.equal(filterAps(list, {noHidden: true}).length, 2);
assert.deepEqual(filterAps(list, {q: "aruba"}).map((a) => a.ssid), ["Corp"]);
assert.deepEqual(filterAps(list, {q: "36"}).map((a) => a.channel), [36]);
assert.equal(sig(list[2]), -70);
console.log("ok");
