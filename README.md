# pywebcnc

A small web dashboard for a Raspberry Pi CNC controller.

![image](/image/cnc.gif)

## What it installs

- Python dashboard on port 8080
- Local JSCut SVG-to-G-code CAM application under `/jscut/`
- Browser terminal WebSocket server on 127.0.0.1:8090
- Nginx on port 80 with WebSocket and JSCut routing
- Python virtual environment with `websockets`
- PM2 processes for the dashboard and terminal

The dashboard provides four tabs:

- Home
- jsCUT
- CNCjs on port 8000
- Kiri:moto
- Files on port 8088
- Terminal through `/terminal-ws`

TODO

 - Web based Firmware builder for GRBLhal
 - Dashboard live status updates

## Requirements

~~The target system should already have a working Node.js/npm installation if PM2 is not installed. This is intentional for ARMv6 systems where distro Node packages may not be suitable.~~

The installer is intended to be run by the user that should own PM2, normally `dietpi`.

## Install

This line clones the repository, runs the installer, and saves a log file. :

```bash
git clone https://github.com/john-clark/pywebcnc.git && cd pywebcnc && bash install.sh 2>&1 | tee install.log
```

The installer uses `sudo` for deployment but must not be run as root. If you are not using a dietpi you should make sure sudo is setup correctly.

After installation the application should be available on the machine web server default port 80.

## PM2 boot persistence

The installer prints the PM2 startup command appropriate for the current user. You can check the service status with:

```bash
pm2 status
```

## Notes

The terminal page currently loads xterm.js and xterm-addon-fit from jsDelivr. If the terminal must work without Internet access, vendor those JavaScript/CSS files into the repository and update `web/terminal.html` to use local copies.

The file server and CNCjs are intentionally not installed by this repository; they are existing services consumed by the dashboard.


### CAM / SVG to G-code

PyWebCNC installs a local copy of JSCut from the upstream `gh-pages` branch and serves it at:

```text
http://PI-IP/jscut/
```

The dashboard's CAM tab loads that local copy, so the CAM application itself does not depend on the public JSCut web page. JSCut is a browser-based CAM tool for converting SVG geometry into CNC toolpaths and G-code, and CNCjs documents JSCut as an SVG-to-G-code CAM option.

### cam-cpp.js

JSCut's current upstream web branch can be missing `js/cam-cpp.js`. The public JSCut page currently reports `Waiting for cam-cpp.js to load`, so simply copying the web branch does not guarantee working operation/toolpath generation. citeturn449240search3turn449240search0

The installer therefore checks for the module and supports supplying a known-good prebuilt copy with:

```bash
JSCUT_CAM_CPP_URL="https://example.invalid/cam-cpp.js" ./install.sh
```

The installer will not claim that the CAM engine is complete when that file is missing.
