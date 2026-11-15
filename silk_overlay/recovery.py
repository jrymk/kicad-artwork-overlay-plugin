"""Bounded private recovery storage; never litter the PCB's project folder."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4


def backup_path(board_file):
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    identity = os.path.normcase(str(Path(board_file).resolve()))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    folder = root / "SilkOverlay" / "recovery" / key
    folder.mkdir(parents=True, exist_ok=True)
    return folder / ("snapshot-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:8] + ".kicad_pcb")


def prune_backups(latest):
    """Keep the last two complete snapshots; ignore any other local files."""
    folder = latest.parent.resolve()
    try:
        snapshots = sorted((path for path in folder.iterdir()
                            if re.fullmatch(r"snapshot-\d{8}-\d{6}-\d{6}(?:-[0-9a-f]{8})?\.kicad_pcb", path.name)
                            and path.is_file() and path.resolve().parent == folder), reverse=True)
    except OSError:
        return
    for path in snapshots[2:]:
        try:
            path.unlink()
        except OSError:
            pass  # A retained snapshot must not turn a successful Apply into an error.
