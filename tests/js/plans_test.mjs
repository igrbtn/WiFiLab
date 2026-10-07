// Plan editor geometry and live-page helpers. Run with node by tests/test_js.py.
import assert from "node:assert/strict";

const {snapPoint, segDist, outside, segLen, hitItem, r2} = await import("../../src/wifilab/static/js/plans.js");
const {splitSeries, trend} = await import("../../src/wifilab/static/js/live.js");
const {cmp} = await import("../../src/wifilab/static/js/lib.js");
const {reportViews} = await import("../../src/wifilab/static/js/report.js");

assert.deepEqual(snapPoint({x: 1.234, y: 2.06}), {x: 1.2, y: 2.1});
const items = [{kind: "wall", x1: 1.03, y1: 1.07, x2: 5, y2: 1.07}];
assert.deepEqual(snapPoint({x: 1.2, y: 1.2}, {items}), {x: 1.03, y: 1.07});
assert.deepEqual(snapPoint({x: 1.2, y: 1.2}, {items, skip: {i: 0, end: 1}}), {x: 1.2, y: 1.2});
assert.deepEqual(snapPoint({x: 4, y: 1.3}, {anchor: {x: 1, y: 1}, ortho: true}), {x: 4, y: 1});
assert.deepEqual(snapPoint({x: -1, y: 9}, {w: 5, l: 3}), {x: 0, y: 3});
assert.equal(segDist(0, 5, -10, 0, 10, 0), 5);
assert.equal(segDist(13, 4, -10, 0, 10, 0), 5);
assert.equal(segLen({x1: 0, y1: 0, x2: 3, y2: 4}), 5);
assert.equal(outside(items, 4, 2).length, 1);
assert.equal(outside(items, 5, 2).length, 0);
assert.equal(hitItem(items, 3, 1.2, 0.2).i, 0);
assert.equal(hitItem(items, 3, 2, 0.2), null);
assert.equal(r2(1.005 + 0.0001), 1.01);

// Signal chart: a gap longer than the threshold breaks the line
assert.deepEqual(splitSeries([{ts: 0}, {ts: 10}, {ts: 100}, {ts: 110}], 30).map((s) => s.length), [2, 2]);
assert.deepEqual(splitSeries([], 30), []);
assert.equal(trend([-80, -80, -80, -80, -60, -60, -60, -60]), 1);
assert.equal(trend([-60, -60, -60, -60, -80, -80, -80, -80]), -1);
assert.equal(trend([-60, -61, -60, -61]), 0);
assert.equal(trend([-60]), 0);

assert.ok(cmp(1, 2) < 0 && cmp("b", "A") > 0 && cmp(null, 1) > 0 && cmp(3, undefined) < 0);

const v = reportViews({aps: []}, "");
assert.deepEqual(v.map((x) => x.kind), ["rssi", "snr", "count", "overlap"]);
const v2 = reportViews({aps: [{id: "a"}]}, "Corp");
assert.deepEqual(v2.map((x) => x.kind), ["rssi", "rssi", "snr", "count", "overlap", "serving"]);
assert.deepEqual(v2[1].target, {type: "ssid", value: "Corp"});
console.log("ok");
