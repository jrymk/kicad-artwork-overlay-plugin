"""KiCad 10 action plugin. Geometry modules are also usable in offline tests."""
import os
import sys
from pathlib import Path

package_dir = Path(__file__).parent
pointer = package_dir / "dependencies.txt"
vendor = package_dir / pointer.read_text().strip() if pointer.is_file() else package_dir / "vendor"
if vendor.is_dir():
    sys.path.insert(0, str(vendor))

if not os.environ.get("SILK_OVERLAY_NO_REGISTER"):
    # KiCad tracks modules imported at registration time, but these heavier
    # modules are lazy-loaded on invocation. Refresh Plugins must refresh them.
    for suffix in ("composer", "group_ui", "rule_ui", "color_ui", "branding", "refresh", "jobs", "stack", "project", "inversion", "recovery", "placement", "board", "groups", "geometry"):
        sys.modules.pop(__name__ + "." + suffix, None)
    from .action import SilkOverlayAction, SilkOverlayUpdateAction
    SilkOverlayAction().register()
    SilkOverlayUpdateAction().register()
