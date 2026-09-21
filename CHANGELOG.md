## 2026.09.21.1

### Added
- Initial release.
- UI config flow for a USB serial port or a serial-over-network URL (`socket://` raw, or `rfc2217://` handled by a built-in Telnet/RFC2217 client), several PRT3 modules supported.
- Automatic discovery of areas and zones with the names stored in the panel (CP852); default-named ones are left disabled and can be enabled in the options.
- Push-driven zone state from the panel's events, with a 5 minute poll to correct drift; lost read queries are retried once.
- Alarm control panel per area (arm away/home/night, disarm); the user code is asked at every action and never stored.
- Binary sensors for zones (device class guessed from the label), diagnostic zone and area flags, panel link, 30 virtual PGMs, and a last-event sensor.
