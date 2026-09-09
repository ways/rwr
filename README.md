# RWR
A web based Radar Warning Receiver for mobile phones.

![Screenshot](/screenshot.png)

[Demo](https://falkp.no/rwr/)

## Explanation
For fun and aviation nerding. This "RWR" simulator has three modes: ground, air and public transport.

Click an object to target it. Hold top left to show real object names. If you get to close to a target, it will spike you.

### Ground
The RWR shows objects from openstreetmap. Supermarkets are shown as "DS" (SAM sites), fuel stations are "4" (MAD4, long range missiles), antennas are "EW" (early warning radar).

### Air
The RWR shows objects from [OpenSky Network](https://opensky-network.org/api), refreshed every 2s. Ranges 10KM/50KM.

### Public transport
The RWR streams live vehicle positions from [Entur](https://developer.entur.no/docs/open-services/vehicle-positions) over a WebSocket push (`graphql-transport-ws`), within a bounding box that follows you. Ranges 0.5KM-5KM. Transport modes map to threat symbology: buses/coaches are "F" (or "F+" when their position is estimated, i.e. `monitored=false`), metro and trams are "SA", trains are "AE", and boats/ferries are "MC". All Entur requests identify with the `ET-Client-Name` header (default `rwr-rwr`, override with `?etclient=`).

## Build container image
- Copy font to fonts/
- Download sound files from VTOL VR wiki and place in sounds/
- A small python backend is needed as a proxy to OSM. Build and run: `podman build -t rwr . && podman run -it --rm -p 8000:8000 rwr`

In .env add API keys. To protect the daily credit budget, the backend tracks `X-Rate-Limit-Remaining` and stretches the cache TTL when fewer than 100 credits are left, and honors `X-Rate-Limit-Retry-After-Seconds` after a 429 so it stops querying until OpenSky allows retries.

## Font
https://github.com/0x408/hornet-display

## Inspiration
F-16, F-18 and [Vtol VR](https://vtol-vr.fandom.com/wiki/Radar_Warning_Receiver_(RWR))

## Disclaimer
Vibecoded.
