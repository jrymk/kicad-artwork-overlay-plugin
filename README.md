# Big Silkscreen Artwork Overlay Tool

By **jerrymk** · Version **1.0.0** · GPL-3.0-or-later

Created with AI assistance and not independently audited.

KiCad **10** action plugin for composing a large SVG directly over a PCB in a
popup window. No preliminary artwork import or Transform It step is needed.

## Use

1. Open a PCB in KiCad. Run **Tools → External Plugins → Big Silkscreen Artwork Overlay Tool**
   (or its toolbar button).
2. Choose an SVG. Drag the artwork to position it; drag a blue corner to resize
   while preserving aspect ratio. Rotation, width and centre coordinates also
   have numeric controls. Right-drag pans; the wheel zooms around the pointer.
3. In **Rules**, click an object to populate a condition, edit its
   wildcard if needed, choose a color button, and **Add** the rule. New projects
   start with the built-in basic rules described below; saved projects keep their own settings. Each color has
   layer toggles, a clearance for each layer, and one flow-inside setting.
   Optionally clip to the board outline and internal cutouts with edge clearance.
4. White shows final generated ink and existing silkscreen; removed artwork is hidden. Colors show
   the **applied exclusion areas**, including clearances and blocked interiors.
   Rule colors are opaque; turn off **Show rule colors** for a realistic
   preview with the same cutouts. Existing silkscreen is drawn white. Copper
   is shown by default; uncheck **Show pads** to hide it.
   Blue handles on the Artworks tab show artwork bounds.
5. **Apply** adds/replaces the generated footprints at those exact board
   coordinates. Save the PCB normally in KiCad. Cancel leaves the board alone.

The Artworks tab is a list of SVGs. **Add SVG** adds a placement; **Remove SVG**
removes it from the pending stack. Switching rows preserves unsaved placement
edits and reuses loaded SVGs and board geometry. Every SVG uses the same rules,
edge clearance and minimum feature width. Overlapping SVGs are stacked as ink;
generated artwork never becomes an exclusion for another SVG.

**Apply** regenerates the whole stack together. Each SVG remains
an independently movable board-only footprint. Its hidden `SilkOverlay` field
stores source/placement plus a synchronized copy of the shared project settings
(version 3). All SVGs still use one common rule set; these copies are recovery
metadata, not independent per-artwork rules. Footprint metadata is authoritative,
so a stale or missing `SilkOverlayProject` board property cannot erase rules. Existing version-1 artwork is read
without modification and supplies the initial shared settings on its next Apply.
Missing/inconsistent saved rules block quick update and Apply until a profile
is loaded or Load basic defaults is used. Quick update refuses an empty rule set unless an inversion layer is configured.
No migration picker is used. Move/rotate/flip operations performed in KiCad are
incorporated when reopening or updating.

After moving components, use the second toolbar action, **Update Big Silkscreen Artwork Overlay Tool**, to reload all saved SVGs and regenerate their cutouts without opening
the composer. It uses the same pipeline as Apply, including both sides and
minimum-width cleanup. A small cancellable progress dialog is shown. All outputs
are prepared before replacement; a missing SVG or geometry error leaves all old
artwork intact. Save the PCB normally afterward. If no saved artwork exists,
the action explains that you should add it with the composer first.

Select **Front / Back on the Artworks tab**. New artwork defaults to Front;
the active KiCad layer at invocation does not select the side. Existing overlays
restore their saved side. Both board sides are supported. The popup shows PCB XY coordinates as
viewed from the front, including when composing on the back. Horizontal mirroring
is an explicit control. Positive rotation is counter-clockwise.

## Artwork Inversion Silkscreen Layer

On **Rules**, under **Artwork Inversion Silkscreen Layer**, choose a layer for
**Front** and/or **Back**. Graphics on this layer become silkscreen after processing.
Wherever they overlap the SVG artwork, both are cut away. Existing exclusion rules,
edge clearance, and minimum-feature cleanup still apply.

Both default to **None**. For example, choose User.3 for the front and draw a
filled polygon or import graphics onto User.3 in KiCad. Within those shapes,
the combined front SVG ink becomes empty and empty space becomes silkscreen.
Outside the shapes, SVG artwork is unchanged. Overlapping SVGs are combined
first, so their order does not affect inversion.

Inversion uses the layer's actual ink: filled polygons, strokes, text glyphs,
and filled zones. An unfilled outline inverts only its stroke, not its interior.
Overlapping shapes on the source layer merge into one inversion area.

The inverted result then goes through the existing exclusion rules, edge
clearance, and minimum-feature cleanup. This also applies to new ink outside
the SVGs. Inversion has no separate clearances or color assignments. A side
with an inversion layer can generate silk even without any SVG on that side.
Existing board silkscreen and the inversion source shapes remain unchanged.

Each SVG keeps its own generated footprint. Extra ink created by inversion
is stored in one additional **Silk inversion** footprint per enabled side;
these footprints are managed automatically and do not appear in the SVG list.
Apply and quick update rebuild them together with the SVGs, always excluding
all generated footprints from board inputs. Disabling inversion and applying
removes its generated footprint. Move/edit the source layer shapes rather
than the generated inversion footprint, whose position is rebuilt on update.

The two selections are shared project settings, saved in the PCB metadata and
profiles. Older projects/profiles have inversion off; Load basic defaults also turns it off.
During dragging, inversion is calculated by the background worker after a
short idle delay; the preview retains the last result with an updating message.
Apply always reads the current board layers before generating the final ink.

## Minimum feature width

On **Rules**, check **Minimum feature width (mm)** to remove thin remaining ink
after all clearance and board-edge cutouts. Uncheck it to skip cleanup while keeping
the entered width. A width of 0 also disables cleanup. New projects start with cleanup
enabled at 0.15 mm; existing projects keep their saved value and enable state. The value and checkbox state are
shared by all SVGs and saved in footprint metadata and rule profiles.

Check **Edge clearance (mm)** to clip artwork to the board outline and internal
cutouts with the entered clearance. Uncheck it to skip outline clipping; object
exclusions still apply. Its saved value is retained. This replaces the separate
clip checkbox; older clipping settings are preserved.

Cleanup operates on the combined ink on each side, so overlapping SVGs can form
a wider feature. It uses a round morphological opening: shrink ink by half the selected
width, then expand it by the same amount. Thin islands, long narrow fragments
and tips disappear; sharp corners become rounded. The result is intersected
with the pre-cleanup ink so it cannot grow into exclusions or outside the board.
It removes ink only: it does not fill gaps, alter existing silkscreen or change
the SVG file. This is a geometric cleanup threshold, not a minimum-width DRC
guarantee for every possible neck, corner or feature.

During movement the canvas keeps the fast uncleaned composition and labels it
**Sliver removal pending**. After a short idle delay, the existing single worker
computes the cleaned preview. Stale results are ignored; Apply recalculates from a fresh board/SVG snapshot. Preview and Apply share the same final geometry pipeline. Fully excluded SVGs retain their source and placement but emit no ink. This lets
Update remove obsolete ink now and restore it after components move again.

## Object color groups

The **Rules** tab places the rule list/editor above the color settings.
There is no board-object table. Click to select a footprint or graphic. Normal
left-drag pans the view; Alt-drag box-selects independent graphics only, excluding
footprints and their child shapes. Shift adds to selection; Shift+Ctrl removes
from selection. Selected objects are filled **#FF00FF magenta**. The canvas hint is:

- Alt drag: Box select graphics
- Shift: Add selection
- Shift+Ctrl: Remove selection

The Artworks tab retains SVG dragging and corner resizing; dragging empty
background pans. Right-drag pans on either tab.
A pick fills the rule editor and changes its type
to **Footprint** or **Graphic**, retaining your last match-field choice for that
type. Multiple picks are retained by **Selected graphics / Selected footprints**;
a mixed selection uses **Selected objects**. Picking never creates a rule
automatically. Footprint-owned silk selects the whole footprint. Independent
board graphics take priority over overlapping footprint interiors.
The spatial index is reused during selection. Controls stack and wrap in the
scrollable sidebar to accommodate display scaling. The window opens at up to
1600 x 1100 logical pixels, capped at 94% of the monitor work area; the settings
pane starts at up to 600 logical pixels wide. Drag the divider to resize it.

Eight color buttons in two rows (including Orange and Purple) below the rule editor
select which color's layer settings you are editing. Both color selectors use
four buttons per row. The buttons in the rule editor choose the rule's assigned
color. Both selectors show the color's **Label**, falling back to its color name when empty.
A checkmark and bold text mark the selected button. The table uses saturated, medium-dark colors
and keeps background shading on the editing row when focus moves away.
Click empty table space to deselect; Update, Remove and reorder buttons then disable.
Objects use the matching rules shown in the editor, or retained manual assignments.
New projects start with visible basic rules for footprints and graphics. Existing
saved rules, including an intentionally empty rule list, remain unchanged.
There are no hidden fallback color assignments.

Each color has these independent exclusion sources:

| Layer source | Geometry |
| --- | --- |
| Cu ∩ Mask | Actual exposed copper: intersection of copper and mask openings on the selected side |
| Silkscreen | Glyph contours, graphic strokes and filled silk shapes on the selected side |
| Courtyard | Footprint courtyard contours on the selected side |
| Mask | All mask openings, including areas without copper |
| Custom layer 1 / 2 | Two explicitly chosen board layers per color, each with Front / Back / Both artwork applicability |

Toggle each source and set its own clearance in millimetres. Disabled sources
do not exclude anything, and blank clearances in disabled sources do not block
Apply. An invalid enabled clearance identifies its color and layer; calculation
errors also appear on the Artworks tab rather than leaving a pending-cleanup message.
Each custom slot has its own clearance and artwork-side
selector. The dropdown starts with an explanatory "Apply to artworks on layer..."
item; selecting it preserves the prior setting. Both is the default, and the
hover text identifies Front as F.SilkS and Back as B.SilkS. Custom uses the exact named layer; it does not swap automatically when
changing side. For example, User.1 with Front selected cuts front artwork only.
Standard Cu, silk, courtyard and mask sources already follow the artwork side.
Old custom-layer settings become slot 1 with Both selected; slot 2 starts disabled.
A missing/disabled custom layer is reported when needed for the active artwork side,
and Apply remains disabled until corrected.

The basic preset enables Silkscreen for Blue and Cu ∩ Mask plus Silkscreen for
Red. Other sources are disabled, and flow-inside is off for all colors. Enable
additional sources as needed when assigning another color. Existing projects
and profiles retain their own color settings.

For example, add a passive-footprint rule assigned to yellow, then adjust exposed
copper/mask clearance until the expanded pads join across the desired gaps
(approximately half the facing edge-to-edge gap). Courtyard plus flow-inside off
protects whole module interiors. Missing/malformed courtyards use a pad/graphic
hull fallback, which may not cover the full component body; its source is retained
in the footprint record.

Footprint silk/text belongs exclusively to that footprint's Silkscreen layer;
Graphic rules only match independent board objects. Each footprint and all its
layers share one color. Old child-specific assignments/rules no longer override
the owner. Green and blue have flow-inside off
in their templates, useful for enclosed badges:
turning flow-inside off fills closed interiors while preserving the
outside contour. Assign all separate segments of a border to the same color.
It does not invent a bounding box around open shapes or a line of text. Text is
converted internally to its glyph strokes; the original PCB text stays editable.

Flow-inside applies **after combining the object's enabled layers and their
individual clearances**. Off fills enclosed voids; on retains them. It never
removes the enabled layer contours/openings themselves. Expanded silk and
openings can jointly close voids within one footprint. Unrelated footprints do
not form a shared filled enclosure. Turn off Courtyard and
Cu ∩ Mask to use mask openings alone (plus any separately enabled silk/custom).
Standalone openings are included under Graphic. Saved color settings and
explicit rules are retained; there are no implicit default colors for openings.

Colors are preview/rule labels on existing objects only. Output remains ordinary
single-layer silkscreen, grouped into one board-only footprint. Cancel discards
rule edits; Apply saves them with the overlay without recoloring PCB objects.

## Matching rules and profiles

Settings are stored in the **PCB file (.kicad_pcb)** when you click Apply: each
generated footprint carries the shared configuration in its hidden `SilkOverlay`
field, and the board also stores a `SilkOverlayProject` property. Save the PCB in
KiCad to persist these changes to disk. The footprint copies are authoritative;
they are not independent rulesets. A saved profile is a separate JSON file for
reuse on other boards, not an automatically selected personal default.

The **last matching rule wins**. Footprint conditions can use footprint name,
reference, value, **Selected footprints**, or **No conditions** (all footprints).
Graphic conditions are **All graphics**, **Text**, or **Selected graphics**.
Plain shapes have no text/name to match: Selected graphics captures their UUIDs
from the canvas without showing a meaningless UUID wildcard input. Text graphics
can be matched by text. The editor only offers Footprint and Graphic as object
categories; older profiles with narrower types are retained when loaded.

Choosing **Any object** provides a condition-free root rule.
Place it above specific rules to use it as a fallback. No root rule is created
automatically. Move a rule downward to give it higher priority.

Matching ignores case; `*` matches any text, `?` one character, and shell-style
character sets are supported. Picking a footprint while **Footprint name** is
selected fills its library/name (for example, a QFN16 package), which you can edit
to `*QFN*` before Add. Use the arrow buttons to reorder rules.

Selected footprints/graphics stores an exact UUID list for the whole selection,
without regex or wildcard interpretation. This survives reference renaming,
movement and rotation; deleting and recreating the object gives it a new UUID.
Reference patterns remain useful for reusable families such as `R*`. Retained
manual assignments from older overlays take precedence over rules; **Clear manual
overrides** removes those assignments and appears only when they exist. It does
not remove matching rules. UUID selection rules are board-specific.

**Save profile…**, **Load profile…**, and **Load basic defaults…** are at the top
of **Rules**. Minimum feature width, edge clearance and clipping are on
that page too. Save/load use portable JSON containing ordered rules, all eight color
settings (labels, layer toggles/clearances, both custom slots and their sides,
and flow-inside), front/back inversion layers, and shared minimum width/edge values and enable states. SVG placements and manual object overrides
are excluded. Loading validates everything before changing settings and clears
manual overrides so the loaded rules take effect. Older profiles without the
shared width/edge fields leave those values unchanged. UUID rules remain
board-specific. Save your own default profile and load it as a starting point.

**Load basic defaults…** loads the built-in starter preset, not a personal
profile. This same preset loads automatically for a board with no saved overlay
settings. It contains:

- Minimum feature width: **0.15 mm**.
- Graphic, no condition → **Blue**, silkscreen enabled at **0.30 mm**.
- Footprint, no condition → **Red**, exposed copper (Cu ∩ Mask) and silkscreen
  enabled at **0.15 mm** each.

Other exclusion layers are disabled, flow-inside is off, and manual overrides
are cleared. Edge clearance and clipping remain unchanged. Nothing is applied
to the PCB until Apply. Both **Load profile…** and **Load basic defaults…** ask
for confirmation before replacing current settings, with Cancel selected by
default. Invalid profiles are rejected before confirmation or any settings change.
Existing saved configurations are never replaced automatically, even if their
rule list is empty. Saving a profile is independent of Apply;
Cancel does not delete a profile you explicitly saved.

## Preview updates and performance

Automatic updating is always enabled and debounces rule edits and computes exclusions in one background worker.
Only the latest requested result is displayed; pending edits replace older work
instead of queuing many jobs. The previous preview stays visible with an updating
status. Apply is disabled until the latest preview is ready. Closing/Cancel stops
pending work from updating the dialog or applying anything to the board.

Footprint layers are combined under one owner before rule matching. Geometry is
merged once per color instead of repeatedly across the whole board for every footprint. Unchanged
color geometry and completed results are cached. Native board/custom-layer reads
still run on the GUI thread and can take time when opening, changing board side
or custom layer, or checking the current board before Apply. Reselecting the
current SVG does nothing. Switching SVGs on the same side reuses the available
space, parsed sources and completed stack geometry; no native board read or rule
calculation is triggered. Side/custom-layer snapshots and rule results have
bounded caches. Switching to a previously unseen side/custom-layer set still
requires a native read. Apply and the update action always read a fresh snapshot.

There are no Auto update or Update preview controls. **Show pads** and **Show rule
colors** sit at the bottom-left of the shared Cancel/Apply row, visible on both
tabs. Optional **Show pads** defaults on; its drawing geometry is built on demand and cached separately.
**Show rule colors** only repaints, without rebuilding exclusions.

## Install with KiCad Plugin and Content Manager

Use **Plugin and Content Manager → Install from File…**, select
`BigSilkscreenArtworkOverlayTool-1.0.0-windows-kicad10-pcm.zip`, and apply the installation.
This package targets Windows x64 KiCad 10 / CPython 3.11. It bundles NumPy,
Shapely/GEOS and svgelements, so no separate Python installer, process check,
pip invocation, or first-run download is required. Refresh action plugins in the
PCB editor if the two toolbar actions are not shown yet.

### Upgrading from development builds

Version 1.0 uses the package ID `com.jerrymk.artwork-overlay-tool`. In PCM,
uninstall the old **Big Silkscreen Artwork Overlay Tool** / **Silk Overlay Composer**
package (`com.jerrymk.silk-overlay-composer`) before installing this release,
so only one copy of the toolbar actions loads. If you used the manual installer,
remove its `silk_overlay` folder from the scripting plugin search path instead.
Restart the PCB editor after changing packages.

Saved PCB artwork, placement metadata, rules and profiles remain compatible.
Keep the SVG source files: they are needed to regenerate artwork. Installing or
removing the plugin does not delete the artwork already saved in a PCB.

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

## Geometry and preservation

- Reads the active in-memory PCB, including unsaved edits. It does not depend on
  an exported layer's origin, page scaling, or SVG re-import alignment.
- Uses native pad mask expansion, front/back via tenting and expansion, exposed
  track geometry, footprint/board text and graphic strokes, filled zones, and
  the board's solder-mask minimum-web setting. Every generated overlay is excluded
  from board-layer reads and rule matching, so SVGs cannot cut each other away.
- Text cutouts use rendered glyph/stroke contours expanded by the silk clearance,
  not text bounding boxes. Original PCB text remains editable; conversion to
  polygons happens internally for the cutout only.
- Curves are approximated to a nominal 0.005 mm tolerance. Cutout buffers include
  an additional 0.005 mm conservative margin. Preview simplification affects
  drawing only. Polygon holes are fractured into KiCad-compatible graphics and
  preserve cutout holes when stored in the PCB.
- The preview composites artwork, cached board-color clearance shapes and the
  existing features. It does not subtract polygons during dragging/resizing;
  exact polygon subtraction runs in the worker for cleanup previews and Apply.
  Clearance changes rebuild affected
  color geometry. World-coordinate drawing paths survive panning and use cached
  detail levels for zooming. Artwork movement uses a drawing matrix, with motion
  updates coalesced at 16 ms; full artwork polygons are transformed only for the
  debounced cleanup calculation or Apply, never on each pointer event.
- Original SVG remains external and unmodified. Keep it for later regeneration;
  board-relative paths are used when possible. Generated ink remains in the PCB
  if the source SVG is unavailable.
- Output is one board-only footprint per SVG, plus one inversion footprint per
  enabled side, all excluded from BOM and position files.
  No pads, nets, component footprints or existing silkscreen are edited.
- Before Apply or quick update, the current in-memory PCB is snapshotted once in
  `%LOCALAPPDATA%/SilkOverlay/recovery/<board-id>/` on Windows (per-user state
  storage on other systems). Only the latest **two** snapshots are retained per
  board. New backups are not written into project folders. Old project-folder
  backups are left alone. The plugin never saves over the user's PCB; save
  normally in KiCad. Batch replacement rolls back on failure. Shared settings
  also live in the generated footprints, so restored footprint metadata restores
  the rule configuration rather than relying on board properties alone.

## SVG scope and limits

Supports paths, Bézier curves, arcs, basic vector shapes, nested transforms,
solid fills, strokes, and nonzero/even-odd holes. All visible colors become ink:
white objects are **not** interpreted as subtraction. Use real compound-path
holes. Text, images, CSS stylesheets, `use`, masks/clipping paths, gradients,
partial opacity, dashed strokes and filters are rejected with an explanation;
expand those into plain paths in the vector editor first. Nonuniformly scaled
strokes should also be outlined in the vector editor.

An invalid/missing Edge.Cuts outline disables board clipping and is reported in
the popup. This tool does not infer component bodies from 3D models, choose a
fabricator's minimum silk feature size, or replace DRC/Gerber review. Optional
minimum-width cleanup removes thin fragments; choose its threshold for your
artwork and process. Fabricator-specific edits to mask layers are outside its preview.

## License and dependencies

Big Silkscreen Artwork Overlay Tool by jerrymk is licensed under GPL-3.0-or-later; see
[LICENSE.txt](LICENSE.txt). Dependency licenses and notices are included with
the bundled packages; see [THIRD_PARTY.md](THIRD_PARTY.md).
