# Big Silkscreen Artwork Overlay Tool 1.0.0

By **jerrymk**. Compose SVG artwork as PCB silkscreen directly in KiCad.

- Place, resize, rotate and mirror multiple SVGs on the front or back.
- Use eight named color groups and ordered rules to control exclusions for
  exposed copper, existing silkscreen, courtyards, mask and custom board layers.
- Set front/back Artwork Inversion Silkscreen Layers to add ink where artwork
  is absent and remove overlaps, then apply the normal exclusions and cleanup.
- Remove narrow features and clip to the board edge with optional clearances.
- Save/load rule profiles and regenerate artwork with a separate update action.
- Move, rotate or flip generated SVG footprints in KiCad; update uses their
  current placements when rebuilding cutouts.

## Install

**Windows x64, KiCad 10 with its Python 3.11 runtime.**

In KiCad's Plugin and Content Manager, choose **Install from File** and select
`BigSilkscreenArtworkOverlayTool-1.0.0-windows-kicad10-pcm.zip`. Dependencies are bundled.
Do not extract it, and do not install the separate source ZIP through PCM.

The package ID is now `com.jerrymk.artwork-overlay-tool`. Users of development
builds should uninstall the old `com.jerrymk.silk-overlay-composer` PCM package
(or remove the old manual scripting-plugin installation) before installing 1.0.
Restart the PCB editor afterward. Saved PCB artwork, rules and profiles remain
compatible; keep SVG source files for future updates.

The source ZIP and SHA-256 files accompany the installable package.
Licensed under GPL-3.0-or-later. Created with AI assistance and not independently
audited. Review generated artwork before fabrication; this tool does not replace
DRC or Gerber review.
