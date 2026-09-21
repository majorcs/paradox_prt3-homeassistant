# Paradox PRT3 for Home Assistant

<img src="custom_components/paradox_prt3/brand/icon@2x.png" alt="Paradox PRT3" width="128" align="right">

[![CI](https://github.com/majorcs/paradox_prt3-homeassistant/actions/workflows/ci.yml/badge.svg)](https://github.com/majorcs/paradox_prt3-homeassistant/actions/workflows/ci.yml)
[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=majorcs&repository=paradox_prt3-homeassistant&category=integration)

Native Home Assistant integration for the Paradox **PRT3** module (Digiplex EVO48/96/192, DGP-848, DGP-NE96) using its ASCII protocol over the USB/serial port.

## Panel setup
Put the PRT3 in Home Automation mode with the ASCII protocol: section `[016]` option 1 ON, option 4 ON, options 5 and 6 OFF, and set the baud rate (options 2/3) to match the integration (default 57600). See Paradox's "PRT3 Printer Module: ASCII Protocol Programming Instructions".

## Installation
Add `https://github.com/majorcs/paradox_prt3-homeassistant` to HACS as a custom repository (category *Integration*), install **Paradox PRT3**, restart Home Assistant, then add the integration from *Settings > Devices & services*.

## Configuration
Everything is done in the UI. Enter the serial port (prefer `/dev/serial/by-id/...`) or a serial-over-network URL such as `socket://192.0.2.10:4000`. The integration reads all area and zone labels from the panel (this takes about half a minute). Areas and zones with factory-default names (`Zone 22`) are not enabled; change the selection in the integration's *Configure* dialog. Several PRT3 modules can be added.

Only one program may use the serial port at a time: stop other gateways (for example an MQTT bridge) first.

## Remote serial (PRT3 on another machine)
If the PRT3 is plugged into a different computer (for example a Raspberry Pi), expose its serial port over TCP with `ser2net` and enter the URL in the integration. Replace the device path, `192.0.2.10` (the machine with the PRT3) and `192.0.2.20` (your Home Assistant host) with your own values, and set the baud rate to the one programmed in the PRT3 (section `[016]`).

The PRT3 accepts one connection at a time, so stop any other program that uses the port (for example an MQTT gateway).

### ser2net 3.5 (Debian Buster and older): `/etc/ser2net.conf`
Raw TCP on port 4000 and RFC2217 (telnet with remote control) on port 4001, both reachable only on the LAN address. Use one or both:
```
# TCP port : mode : timeout : device : serial options
192.0.2.10,4000:raw:0:/dev/serial/by-id/usb-PARADOX_PARADOX_APR-PRT3_XXXXXXXX-if00-port0:57600 8DATABITS NONE 1STOPBIT kickolduser
192.0.2.10,4001:telnet:0:/dev/serial/by-id/usb-PARADOX_PARADOX_APR-PRT3_XXXXXXXX-if00-port0:57600 8DATABITS NONE 1STOPBIT kickolduser remctl
```
Both lines share the same device, so only one of the two ports can have a client at a time. ser2net 3.5 rejects the `local` option (`Unknown config item: local`), so it is not used here.
Apply with `sudo systemctl restart ser2net`.

IP source filtering with TCP wrappers (the service name is `ser2net`; check that your build has it with `ldd $(which ser2net) | grep wrap`):
```
# /etc/hosts.allow
ser2net : 192.0.2.20

# /etc/hosts.deny
ser2net : ALL
```

### ser2net 4.x (Debian Bullseye and later): `/etc/ser2net.yaml`
Raw TCP:
```yaml
%YAML 1.1
---
connection: &prt3
  accepter: tcp,192.0.2.10,4000
  connector: serialdev,/dev/serial/by-id/usb-PARADOX_PARADOX_APR-PRT3_XXXXXXXX-if00-port0,57600n81,local
  options:
    kickolduser: true
```
For RFC2217 change only the accepter to `accepter: telnet(rfc2217),tcp,192.0.2.10,4001` (in a second `connection:` block if you want both).
Apply with `sudo systemctl restart ser2net`. Debian builds of ser2net 4.x do not necessarily include TCP wrappers, so use the firewall for source filtering.

### Source filtering with a firewall (any ser2net version)
Allow only the Home Assistant host to reach the port:
```
# iptables
sudo iptables -A INPUT -p tcp --dport 4000 -s 192.0.2.20 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 4000 -j DROP
sudo apt install iptables-persistent      # keeps the rules after a reboot

# or ufw
sudo ufw allow from 192.0.2.20 to any port 4000 proto tcp
```

### Home Assistant side
Enter `socket://192.0.2.10:4000` (raw) or `rfc2217://192.0.2.10:4001` (RFC2217) as *Serial port or URL*. With `socket://` the baud rate is fixed in the ser2net file and the baud field in Home Assistant has no effect; with `rfc2217://` the integration sets the baud rate, 8N1 on the remote port. Both were tested against ser2net 3.5 (the 4.x examples are untested). `rfc2217://` is implemented inside the integration because pyserial's own handler does not work with asyncio.

- There is no authentication in either mode, so keep the filtering above in place: anyone who can reach the port can read zone activity and try arm/disarm codes.
- If the link drops, entities become unavailable and the integration reconnects by itself (retry delay up to 60 s).
- Not covered: USB/IP, which makes the device appear locally. It works if you set it up yourself and pick the resulting `/dev/...` path.

## Entities
- **Alarm control panel** per area: arm away (regular), arm home (stay), arm night (instant), disarm. The user code is asked at every action and is never stored.
- **Binary sensor** per zone, with a device class guessed from the label (motion, door, window, tamper, smoke). The protocol does not report the zone type, so change the class in the entity settings if the guess is wrong.
- Diagnostic sensors (disabled by default unless noted): per-zone tamper, fire loop trouble, alarm, fire alarm, supervision lost, low battery; per-area not ready and trouble (enabled), zone in memory, in programming, strobe; panel link; 30 virtual PGMs; last event (in words, e.g. `Zone open: Kitchen door (zone 4), Ground floor`, with the raw `G001N004A002` code as an attribute) and last event time.

**Database size:** every panel event changes the *Last event* and *Last event time* sensors, so they write a state row per event. Their attributes are already kept out of the recorder, but Home Assistant only lets you exclude an entity's states in `configuration.yaml`. If you don't need their history, add:
```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.paradox_prt3_*_last_event*
logbook:
  exclude:
    entity_globs:
      - sensor.paradox_prt3_*_last_event*
```
(the glob follows your device name; the sensors still show the live value and can trigger automations).

Zone changes are pushed by the panel in real time; a poll every 5 minutes corrects drift.

## Releases and development
Versions are dates: `YYYY.MM.DD.N` (N counts releases on the same day). Changes reach `main` through pull requests that must pass the `test`, `hassfest` and `hacs` checks; pushing a tag equal to the `manifest.json` version publishes the GitHub release from `CHANGELOG.md`.

Licensed under the MIT license.

```
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest          # enforces 90% coverage
.venv/bin/python -m pytest tests/test_protocol.py --no-cov
```
