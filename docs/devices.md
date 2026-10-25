# Devices and location

HAYAT reads both of these from the computer. It does not recall or estimate them.

## What is plugged in

Say "list my devices", "what devices are connected to this computer" or "show bluetooth devices".

`list_devices` reads Windows Plug and Play, the same list Device Manager shows. It groups devices into cameras, microphones and speakers, Bluetooth, USB, displays and graphics, printers and scanners, network adapters, keyboards and mice, drives, battery and ports. It hides Windows plumbing such as enumerators, WAN miniports and Bluetooth service stubs, names any device that reports a problem, and shows the battery level. Listing a device opens nothing, so the camera light stays off. Permission: `system_read`.

## Where this computer is

Say "where am I", "where are we located now" or "what's my location". A bare "where are we?" reads the location too, unless a lesson is open (then it is about the lesson). Left to the model on 2026-10-07, it guessed a city from the clock's time zone.

`device_location` tries two methods, and the reply always says which one it used:

1. **Windows Location service.** This uses GPS, Wi-Fi or cell positioning and gives its own accuracy radius (for example "accurate to about 135 m"). Since 2026-10-17 the town is named on this computer from GeoNames' list of about 144,000 places, so the coordinates are not sent anywhere (until then they went to BigDataCloud's reverse geocoder). Say "set up private place names" once to install the list (2 MB, hash-pinned). See [Location privacy](location-privacy.md). OpenStreetMap's Nominatim and Photon disallow automated use in robots.txt, so HAYAT doesn't use them.
2. **Public IP lookup.** This is the fallback when Windows location is off or denied. It asks ipwho.is (then ipapi.co), which see only this computer's public IP address. The answer is city level at best and is often tens of kilometres off. A VPN or a mobile network shows where that network exits, not where you are.

If neither works, HAYAT says no location could be read; it never guesses. To get the exact method, open Settings > Privacy & security > Location and turn on both Location services and "Let desktop apps access your location".

Permission: `computer_observe`, a sensor category, so privacy mode switches location off along with the camera and screen.

## Wi-Fi sensing

Channel state information (CSI) devices, such as an ESP32 on a COM port, are connected, checked and recorded through the `csi_*` tools. See [Wi-Fi sensing](csi.md).

## Why these exist

On 2026-10-05 HAYAT was asked "where are we located now" and answered "Charlotte, North Carolina". It cited an IP trace that never ran, and the real Windows fix was in another state. The same evening, conversation replies claimed that cold emails had been sent and that a CSV of "verified" leads had been saved, but no tool had run. Every model-written reply is now checked by [app/action_claims.py](../app/action_claims.py). A first-person claim of a send, file, schedule, verification or lookup in a reply that ran no tools triggers a truthful rewrite. If the rewrite still makes the claim, the reply starts with "⚠ Not done". Earlier replies in the history get the same correction attached, so later turns stop building on them.
