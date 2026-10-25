# Router and home network

HAYAT can show the Wi-Fi router this computer is connected to: what it is, how good its signal is, how busy the air around it is, and which devices have recently been active on the network. Everything is read-only. HAYAT sends no password, changes no setting, and contacts only the default gateway that Windows reports. The code is in [app/network.py](../app/network.py).

## Say it in chat

| You say | Tool | What you get |
| --- | --- | --- |
| "Check my router", "how is my internet?", "5G signal", "which Wi-Fi am I connected to?" | `network_router` | The router, the Wi-Fi link, IP details, the router's own status and latency |
| "Show nearby Wi-Fi networks", "best Wi-Fi channel" | `network_nearby_wifi` | The access points in Windows' last scan, and how crowded your channel is |
| "Who is on my Wi-Fi?", "what devices are connected to my network?" | `network_devices` | The devices recently seen on this network |
| "Update device vendor names" | `network_vendor_update` | Downloads the IEEE maker list once (about 4 MB) |

"What is a router?" is still answered in the conversation. "List my devices" still lists this computer's own hardware ([Devices and location](devices.md)).

Typed fast is fine (2026-10-15): "chek my routr", "whos on my wifi", "list my devises", "is csi conected" and "any erthquakes near tokyo" reach the same tools. [app/intent/typos.py](../app/intent/typos.py) expands chat shorthand and corrects misspelt tool words, but the router uses the corrected words only when the original message matched no tool and the corrected one does, so an ordinary chat message keeps its own words. "Am I safe?" and "what's happening around me" go to the hazard radar.

## Where each fact comes from

- **Connection**: `netsh wlan show interfaces` gives the network name (SSID), access point, band, channel, radio type, signal and receive rate. Windows' IP configuration gives this computer's address, the gateway and the DNS servers.
- **The router**: HAYAT reads the gateway's front page (only its title) and, for routers that publish one without a login, a status page. The **T-Mobile 5G Gateway** (`/TMI/v1/gateway?get=all`) gives the maker, model, firmware and whether it is up to date, uptime, and the 5G/LTE signal: band, bars, cell and tower IDs, and RSRP, RSRQ, SINR and RSSI. These are rated in plain words:

  | Measure | Excellent | Good | Fair | Poor |
  | --- | --- | --- | --- | --- |
  | RSRP (strength) | -80 dBm or better | down to -90 | down to -100 | below -100 |
  | RSRQ (quality) | -10 dB or better | down to -15 | down to -20 | below -20 |
  | SINR (cleanliness) | 20 dB or better | 13 to 20 | 0 to 13 | below 0 |

  The overall rating is the worst of the three. A router that publishes no such page still shows its address, MAC address and admin-page title. The gateway is contacted only when its address is a private one.
- **Nearby Wi-Fi**: `netsh wlan show networks mode=bssid` lists Windows' last scan, with signal, band, channel and, where the access point advertises it, connected stations and channel utilisation. HAYAT says how many other networks share your channel. It suggests the least crowded channel in the band only when one is clearly quieter. Your router picks its channel itself unless you set it by hand.
- **Devices**: these come from Windows' IPv4 neighbour (ARP) cache, so they are the devices this computer has exchanged traffic with recently. HAYAT sends no scan, so quiet devices may be missing. Phones usually use a private, randomised MAC address, and HAYAT reports these as such rather than guessing a maker. Other makers come from the IEEE MA-L registry once you have downloaded it. It is saved to `data/network/oui.csv`, inside the HAYAT folder.
- **Latency**: the time to open a TCP connection to the router (port 80) and to a public resolver (1.1.1.1:443). No data is exchanged.

## In the window

Open **Studios → Router & home network**, or the **Router & network** tab of the Wi-Fi sensing window. The tab refreshes every 20 seconds while it is showing, and when you press Refresh. It has four panels:

- **Router & connection**: the model, firmware, uptime, the Wi-Fi link, this computer's address, DNS and latency. **Open admin page** opens the router's own page in your browser, where you sign in yourself if you want to.
- **5G / LTE signal**: bars, band, cell and tower, with SINR, RSRP and RSRQ coloured by their rating. A chart shows SINR and Wi-Fi signal over the last refreshes.
- **Wi-Fi around you**: the nearby access points (yours is marked ●) and the channel advice.
- **Devices on this network**: address, MAC, maker (or "private (randomised) address") and when each was last seen. **Update vendor names** downloads the IEEE list.

## Permissions

`network_router`, `network_nearby_wifi` and `network_devices` only read this computer's state and its own gateway (`system_read`). `network_vendor_update` is a public download (`public_download`), so modes that keep HAYAT offline block it.

## Network guard: new devices on your Wi-Fi

HAYAT remembers every device it has seen on your network, in `data/network/known-devices.json`. When a device it has never seen appears, it tells you.

- **First look**: HAYAT records what is already there, so nothing is reported that time.
- **While the main window is open**: it checks every 2 minutes, starting a minute after the window opens. A new device gets one chat note, for example: "New device on your network: 192.168.12.240 (a private (randomised) address, usually a phone, MAC …)". Privacy mode pauses the checks.
- **Ask any time**: "any new devices on my wifi?", "network guard" or "unknown devices" (`network_guard`).
- **Name a device**: say "name device 192.168.12.203 as living room TV", using its IP or MAC (`network_name_device`), or select it in the router tab and press **Name device…**. The name then shows in the device list and in later answers.

The guard reads the same neighbour cache as the device list, so it sends nothing. A device that has not exchanged traffic on the network recently stays unseen until it does. That is usually within minutes for phones and computers, but can take longer for quiet gadgets.
