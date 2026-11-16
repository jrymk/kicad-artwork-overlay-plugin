# Big Silkscreen Artwork Overlay Tool

By **jerrymk** · GPL-3.0-or-later

Code created with AI assistance and not independently audited.

KiCad 10 action plugin for composing a large SVG artwork directly over a PCB.

## Features and Showcase

<img width="2460" height="1542" alt="image" src="https://github.com/user-attachments/assets/3e35c4b2-3d9e-4788-a95c-341207b24342" />
Board example: Raspberry Pi CM5 IO Board

[Video URL](http://s3.jerrymk.com/pub/Screen%20Recording%202026-11-16%20194355.mp4)

---

<img width="2236" height="1227" alt="image" src="https://github.com/user-attachments/assets/b4036b77-6f20-441d-8469-dc78c0a788e8" />
<img width="1857" height="1217" alt="image" src="https://github.com/user-attachments/assets/a38826e5-cced-4d42-9066-e39644bbf138" />
<img width="1857" height="1425" alt="image" src="https://github.com/user-attachments/assets/ee60d694-a4d3-4b17-af8e-5151efc5987e" />


### Video
[Another video URL](https://s3.jerrymk.com/pub/Screen%20Recording%202026-11-15%20153022.mp4)

This video shows you:
- how to get started in adding an artwork to a PCB with default settings
- adjust clearances
- add custom rules to certain graphic objects for a larger clearance
- add custom rules to certain footprint types for allowing artwork to go inside the component
- use a custom layer for extra exclusion objects like User.1
- use the update button to regenerate the overlay artworks without opening the tool window
- move the generated artwork footprint around instead of going back to the tool window to adjust the position

## Install with KiCad Plugin and Content Manager

Use **Plugin and Content Manager → Install from File…**, select
`BigSilkscreenArtworkOverlayTool-1.0.0-windows-kicad10-pcm.zip`, and apply the installation.
This package targets Windows x64 KiCad 10 / CPython 3.11. It bundles NumPy,
Shapely/GEOS and svgelements, so no separate Python installer, process check,
pip invocation, or first-run download is required. Refresh action plugins in the
PCB editor if the two toolbar actions are not shown yet.

### Building a package

From the extracted source ZIP's root directory, use ordinary Python:

```powershell
python build_pcm.py --vendor path/to/expanded-cp311-windows-dependencies --source
```

The vendor directory must contain the versions in `requirements.txt`, expanded
from CPython 3.11 Windows x64 wheels. The builder can also reuse the vendor
folder from an existing PCM installation. Add `--schema path/to/pcm.v2.schema.json`
to validate metadata with `jsonschema` against KiCad's official schema.

The output includes an installable PCM ZIP, a source ZIP and SHA-256 files.
The PCM ZIP follows the official addon layout; it is installed using
**Install from File**, without extracting it. The source ZIP is for viewing or
modifying the code and cannot be installed directly through PCM.

The implementation uses KiCad 10's legacy action-plugin/native geometry bindings.
It does not require enabling the IPC API. Future KiCad major versions may need an
API migration; KiCad 9 is not a tested target.

## License and dependencies

Big Silkscreen Artwork Overlay Tool by jerrymk is licensed under GPL-3.0-or-later; see
[LICENSE.txt](LICENSE.txt). Dependency licenses and notices are included with
the bundled packages; see [THIRD_PARTY.md](THIRD_PARTY.md).
