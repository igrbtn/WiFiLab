// Heatmap helpers: ramps, IDW, fade, fields, AP groups, view values. Run with node by tests/test_js.py.
import assert from "node:assert/strict";

const {ENG_STOPS, ENG_COVER, engColor, ramp, idw, coverAlpha, heatField, heatStep, walkOrder, engGeom, scaleBarM, radioKey, shortMac,
  apGroups, groupLabel, pointValue, targetHit, servingField, apColor, AP_PALETTE, viewValue, NOT_HEARD, toMetres, rulerMarks, SNR_STOPS} =
  await import("../../src/wifilab/static/js/heat.js");

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

// Ramp: exact colours at the stops, clamped beyond the ends, blended in between.
assert.deepEqual(engColor(-35), ENG_STOPS[5][1]);
assert.deepEqual(engColor(-20), ENG_STOPS[5][1]);
assert.deepEqual(engColor(-100), ENG_STOPS[0][1]);
assert.ok(engColor(-40)[2] > engColor(-80)[2], "strong is blue, weak is red");
assert.deepEqual(ramp(SNR_STOPS, 25), SNR_STOPS[3][1]);

// IDW, power 2
const S = [{x: 0, y: 0, v: -40}, {x: 4, y: 0, v: -80}];
assert.deepEqual(idw(S, 0, 0), {v: -40, dmin: 0});
near(idw(S, 2, 0).v, -60);
near(idw(S, 1, 0).v, (-40 / 1 + -80 / 9) / (1 + 1 / 9));
assert.equal(idw([], 1, 1), null);

// Fade: full to 3 m, gone by 5 m
assert.deepEqual(ENG_COVER, {full: 3, zero: 5});
assert.equal(coverAlpha(3), 1);
assert.equal(coverAlpha(5), 0);
near(coverAlpha(4), 0.5);

const f = heatField([{x: 0.05, y: 0.05, v: -50}], 2, 1, 0.1);
assert.equal(f.nx, 20); assert.equal(f.ny, 10);
assert.ok(f.v.every((v) => Math.abs(v + 50) < 1e-4));
const far = heatField([{x: 0, y: 0, v: -60}], 12, 2, 0.5);
assert.equal(far.a[0], 1); assert.equal(far.a[far.nx - 1], 0);
assert.equal(heatStep(12, 8), 0.1);
assert.ok(heatStep(100, 100) >= 0.5);

// A not-heard point pulls to the floor only within the fade radius.
const dead = [{x: 0, y: 0, v: -50}, {x: 10, y: 0, v: NOT_HEARD, missing: true}];
near(idw(dead, 1, 0).v, -50);
assert.ok(idw(dead, 8, 0).v < -80);

assert.deepEqual(walkOrder([{id: "a", ts: 30}, {id: "b", ts: 10}, {id: "c"}]).map((p) => p.id), ["b", "a"]);

const g = engGeom(20, 10, 900);
near(g.rw / g.pxm, 20); near(g.rh / g.pxm, 10);
assert.ok(g.W <= 900);
assert.equal(scaleBarM(20), 5);
assert.deepEqual(toMetres(g, g.ox, g.oy, 20, 10), {x: 0, y: 0});
assert.equal(toMetres(g, 0, 0, 20, 10), null);
assert.deepEqual(rulerMarks(2, 40).filter((m) => m.label).map((m) => m.label), ["0", "1", "2"]);

// Radios
assert.equal(radioKey("02:5A:4C:11:22:3F"), "02:5a:4c:11:22");
assert.equal(shortMac("02:5A:4C:11:22:3F"), "22:3f");
const pts = [{aps: [{bssid: "02:00:00:00:01:00", ssid: "Corp", rssi: -50, channel: 1, band: "2.4"},
  {bssid: "02:00:00:00:01:01", ssid: "Guest", rssi: -52, channel: 1, band: "2.4"},
  {bssid: "02:00:00:00:02:00", ssid: "Corp", rssi: -70, channel: 1, band: "2.4"},
  {bssid: "02:00:00:00:03:00", ssid: "Other", rssi: -78, channel: 6, band: "2.4"}], noise: -90}];
const groups = apGroups(pts);
assert.equal(groups.length, 3);
assert.deepEqual(groups[0].bssids, ["02:00:00:00:01:00", "02:00:00:00:01:01"]);
assert.equal(groupLabel(groups[0]), "Corp 01:00");

// Targets and views
const set = (...b) => ({type: "set", bssids: new Set(b)});
assert.equal(pointValue(pts[0], {type: "best"}).v, -50);
assert.equal(pointValue(pts[0], {type: "ssid", value: "Other"}).v, -78);
assert.deepEqual(pointValue(pts[0], set("aa:00:00:00:00:01")), {v: NOT_HEARD, missing: true});
assert.ok(targetHit(pts[0].aps[0], set("02:00:00:00:01:00")));
assert.equal(viewValue("snr", pts[0], {type: "best"}).v, 40);
assert.equal(viewValue("snr", {aps: pts[0].aps}, {type: "best"}).v, 45, "default noise -95");
assert.equal(viewValue("count", pts[0], {type: "best"}).v, 2, "radios at -75 or better");
assert.equal(viewValue("count", pts[0], {type: "ssid", value: "Corp"}).v, 2);
assert.equal(viewValue("overlap", pts[0], {type: "best"}).v, 1, "one other radio on channel 1");
assert.ok(viewValue("overlap", {aps: []}, {type: "best"}).missing);

// Serving zones
const per = [[{x: 0.5, y: 0.5, v: -40}, {x: 3.5, y: 0.5, v: NOT_HEARD, missing: true}], [{x: 0.5, y: 0.5, v: -80}, {x: 3.5, y: 0.5, v: -45}]];
const sf = servingField(per, 4, 1, 0.5);
assert.equal(sf.idx[0], 0); assert.equal(sf.idx[7], 1);
assert.deepEqual(apColor(AP_PALETTE.length + 1), AP_PALETTE[1]);
console.log("ok");
