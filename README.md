# RWR
A web based Radar Warning Receiver for mobile phones.

![Screenshot](/screenshot.png)

[Demo](https://falkp.no/rwr/)

## Explanation
For fun and aviation nerding. The RWR shows objects from openstreetmap. Supermarkets are shown as "DC" (SAM sites), fuel stations are "4" (MAD4, long range missiles), antennas are "EW" (early warning radar).

Click an object to target it. Hold top left to show real object names. If you get to close to a target, it will spike you.

## Web options
- ?debug=1
- ?endpoint=https://...

## Modes
- **OSM** (default): ground objects from OpenStreetMap/Overpass. Ranges 500M/1KM/2KM.
- **OSAPI**: live aircraft from the [OpenSky Network](https://opensky-network.org/api), refreshed every 2s. Ranges 10KM/50KM. The MODE button toggles between them and the choice is stored in local storage.

The OpenSky upstream is proxied and cached by the python backend. Anonymous access is limited to 400 credits/day (~1 credit per query), so the backend only calls OpenSky at most every `RWR_OSAPI_TTL` seconds (default 240s anonymous). To get fresher data, create a free OpenSky API client (account → API clients) and set:

- `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` — backend then fetches an OAuth2 bearer token and defaults `RWR_OSAPI_TTL` to 10s (4000 credits/day, ~1 credit per bbox query). OpenSky data itself only updates every ~5-15s, so 10s is effectively as fresh as it gets.
- `RWR_OSAPI_TTL` — overrides the upstream cache TTL in seconds.

To protect the daily credit budget, the backend tracks `X-Rate-Limit-Remaining` and stretches the cache TTL when fewer than 100 credits are left, and honors `X-Rate-Limit-Retry-After-Seconds` after a 429 so it stops querying until OpenSky allows retries.

## Build container image
- Copy font to fonts/
- Download sound files from VTOL VR wiki and place in sounds/
- A small python backend is needed as a proxy to OSM. Build and run: `podman build -t rwr . && podman run -it --rm -p 9000:8000 rwr`

## Font
https://github.com/0x408/hornet-display

## Inspiration
F-16, F-18 and [Vtol VR](https://vtol-vr.fandom.com/wiki/Radar_Warning_Receiver_(RWR))

## Disclaimer
Vibecoded.
