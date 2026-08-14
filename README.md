# RWR
A web based Radar Warning Receiver for mobile phones.

[Demo](https://falkp.no/rwr/)

## Explanation
For fun and aviation nerding. The RWR shows objects from openstreetmap. Supermarkets are shown as "DC" (SAM sites), fuel stations are "4" (MAD4, long range missiles), antennas are "EW" (early warning radar).

Click an object to target it. Hold top left to show real object names. If you get to close to a target, it will spike you.

## Web options
- ?debug=1
- ?endpoint=https://...

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
