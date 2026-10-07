# Walking survey guide

A practical walk-through for a passive Wi-Fi survey of one floor with a MacBook and WiFiLab. Plan for about one
minute per point: a 400 m2 office with 40 points takes 45-60 minutes including setup.

## 1. Before you go

- Use the app bundle (`/Applications/WiFiLab.app`) and make sure Location Services is allowed for WiFiLab
  (Settings page: "Location Services: authorized"). Without it every network is anonymous and points are refused.
- Charge the Mac fully: scanning keeps the radio busy for the whole walk. Turn off Low Power Mode, which can
  throttle background work.
- Stay connected to the network you survey (or to none), and do not switch networks during the walk: roaming
  mid-survey changes what the radio reports.
- Settings > Background scanning: keep 15 s or set 10 s. The background scan only feeds Live/History; survey points
  always trigger their own scan.
- Close heavy downloads and video calls: they compete with scans for radio time.

## 2. Prepare the floor plan

1. Surveys > New survey, then Floor plan.
2. Set width and length of the area in metres. Get them from a building plan, a laser meter or by pacing a long
   corridor (count steps x your step length). The scale is everything: a heatmap on a wrong scale puts coverage in
   the wrong rooms.
3. Draw the outer walls, inner walls, doors and windows, or import a plan JSON (Import > Copy LLM prompt turns a
   photo of an evacuation plan into JSON). Check two or three known distances against the rulers.
4. Optional: add the plan image as background to see rooms while you walk.

## 3. Walk and measure

- Point spacing: every 2-4 m in open space and corridors, at least one point in every room, plus points at the
  edges of the area you need covered (far corners, stairwells, meeting rooms behind thick walls). Denser points near
  APs and problem areas.
- Walk a pattern (rows or a snake), not random jumps: the map shows your path and gaps are easy to spot.
- At each spot: stand still, click your position on the plan in Measure mode and keep still until the point
  appears. A fresh scan takes 2-4 s; macOS caches scans for about 10 s, so WiFiLab rescans until the data is new and
  a point can take up to 12 s. If a toast says the scan repeated the previous point, wait a few seconds and measure
  that spot again (Select / move lets you delete the old point).
- Hold the Mac the same way the whole walk: lid open at about 90-110 degrees, held at waist or chest height (roughly
  where people use laptops and phones), screen facing your walking direction. The antennas are in the lid hinge
  area; your body between the Mac and an AP costs 3-6 dB, so do not turn around between points.
- Do not put the Mac on metal carts or desks with steel frames for some points and in your hands for others.
- Keep the lid open: closing it stops scanning.
- Watch the info line under the tools: point count, BSSIDs heard, strongest signal. A sudden drop to a few BSSIDs
  usually means the radio was busy: measure again.

## 4. Place the access points

- Place AP mode: click where each AP hangs and pick its radio from the list of radios heard. A physical AP shows
  several BSSIDs (one per SSID and band) that share the first five bytes: pick the group, not a single BSSID.
- The strongest readings point to the AP, but ceilings and corridors can mislead: confirm with the Find AP page
  (walk until the RSSI peaks) or with the network's controller.
- Placed APs enable the serving AP zones and per-AP heatmaps, also in the report.

## 5. Read the heatmaps

- Signal (RSSI) of the target SSID: -67 dBm or better (green and blue) for voice and video, -70 to -75 dBm is fine
  for data, -80 dBm and below (orange, red) is weak coverage.
- SNR: 25 dB or more for good throughput, under 15 dB is poor even with a decent RSSI.
- AP count: two or more radios at -75 dBm or better give roaming headroom; more than four on one channel is too much.
- Channel overlap: co-channel radios heard at -82 dBm or better. Green (0) is ideal; 2 or more means those APs
  share airtime: change channels or lower transmit power.
- Serving AP zones: which placed AP is strongest where. Large odd-shaped zones or one AP serving far rooms point to
  wrong power settings or a missing AP.
- Colours fade out 3-5 m away from points: blank areas are not "no signal", they are "not measured".

## 6. Report

- Report (from the survey): pick the coverage SSID, then Print / PDF, Word (.docx), HTML, CSV or JSON. Heatmaps are
  included whenever the survey has measured points.
- Export JSON keeps the whole survey (plan, points, APs) and imports back into WiFiLab; export it as a backup after
  the walk.
- Survey data contains real network names and addresses: store and share it like any other customer data.
