# Bundled dependencies

The Windows PCM build contains the following distributions for CPython 3.11 x64:

| Distribution | Version | License |
| --- | --- | --- |
| NumPy | 2.4.6 | BSD-3-Clause, plus bundled library notices |
| Shapely | 2.1.2 | BSD-3-Clause; bundled GEOS is LGPL-2.1-or-later |
| svgelements | 1.9.6 | MIT |

Their original distribution metadata, licenses and native DLLs are included under
`vendor/`, including additional notices for NumPy's bundled numeric libraries.
Big Silkscreen Artwork Overlay Tool by jerrymk is licensed under GPL-3.0-or-later.
The plugin's Python source is included. See LICENSE.txt.

These dependencies are loaded locally. The plugin does not run pip, spawn an
installer, check for active KiCad processes, or download packages during loading.
