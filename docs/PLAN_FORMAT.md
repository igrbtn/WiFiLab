# Floor plan format `fieldtab.plan/1`

WiFiLab stores the floor plan of a survey in the `fieldtab.plan/1` format, shared with the FieldTab tablet and
FieldTab Desk, so a plan drawn in one opens in the others.

- JSON, UTF-8, up to 256 KB.
- Coordinates in metres; origin is the top-left corner of the room, x grows right, y grows down.

```json
{
  "format": "fieldtab.plan/1",
  "name": "Floor 2 east wing",
  "width_m": 24.5,
  "length_m": 12,
  "items": [
    {"kind": "wall",   "x1": 0,  "y1": 0, "x2": 24.5, "y2": 0},
    {"kind": "door",   "x1": 6,  "y1": 4, "x2": 6.9,  "y2": 4, "label": "D1"},
    {"kind": "window", "x1": 10, "y1": 0, "x2": 12,   "y2": 0},
    {"kind": "beam",   "x1": 0,  "y1": 6, "x2": 24.5, "y2": 6}
  ]
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `format` | string | yes | exactly `fieldtab.plan/1` |
| `name` | string | no | up to 64 characters |
| `width_m`, `length_m` | number | yes | room size, 0.5..100 m |
| `items` | array | yes | up to 300 segments |
| `items[].kind` | string | yes | `wall`, `door`, `window`, `beam` |
| `items[].x1..y2` | number | yes | segment ends, inside 0..width_m / 0..length_m (0.01 m tolerance) |
| `items[].label` | string | no | up to 16 characters |

A door or a window is a segment the width of the opening lying on the wall line; the heatmap cuts a gap in the
wall under it. A beam is a segment along its axis.

## Making a plan from a photo

Floor plan > Import > Copy LLM prompt copies the prompt below. Give it to any vision LLM together with a photo or
scan of the plan, paste the answer into the Import box and press Load: WiFiLab finds the JSON object in the answer
(bare, fenced or surrounded by text), validates it and shows readable errors.

> You convert a floor plan image into JSON. Output ONLY a JSON object, no prose, in this format:
> {"format": "fieldtab.plan/1", "name": "...", "width_m": W, "length_m": L, "items": [{"kind": "wall"|"door"|"window"|"beam", "x1": .., "y1": .., "x2": .., "y2": .., "label": "optional"}]}.
> Units are metres. Origin is the top-left corner of the drawing's bounding box; x grows right, y grows down.
> Take sizes from the dimension lines and numbers on the plan; if there are none, use the scale bar; if there
> is neither, assume a standard interior door is 0.9 m wide and say nothing about it. Every wall is one straight
> segment along its centre line (split walls at corners and at T-junctions). A door or window is a segment the
> width of the opening on the wall line. A beam is a segment along its axis. Round to 0.05 m. At most 300 items.
> width_m and length_m are the overall size of the bounding box. Keep every coordinate inside it.

## FieldTab tablet exports WiFiLab imports

| Export | Recognised by | Becomes |
|---|---|---|
| `wifi-survey-*.json` (`"kind": "wifi_survey"`) | `kind` or `aps` + `channels` | AP inventory of a new survey |
| `wifi-survey-*.csv` | `bssid` and `ssid` columns | AP inventory |
| `wifi-map-*.json` (`"kind": "wifi_map"`) | `kind`, or `points` with an area or grid | plan size, plan lines, measured points |
| session document (`"kind": "wifi"`) | `seen` + `map` | points, plan and AP inventory |
| `plan-*.json` | `format` | an empty survey with that plan |

Older cell-only maps (no metres) are read at 0.5 m per cell. Survey > Export FieldTab map writes a WiFiLab survey
back as a `wifi_map` metre map.
