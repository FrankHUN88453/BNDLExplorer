# BNDL Explorer

A Windows Explorer-style viewer and editor for the `.BNDL` bundles of **Need for Speed: Most Wanted (2012)**:
the retail PC game and the November 2011 PS3 prototype (`NPXX00207`). Browse the game folders, open bundles,
look at every resource, edit it, import and export it (drag and drop and copy / paste work with Explorer), and
convert bundles between PS3 and PC.

> **No game files here.** The program only works on your own copies of the game files. Keep backups.

## Running

**Standalone:** download `BNDLExplorer.exe` from the [releases](../../releases) and run it (Windows 10 / 11
x64, nothing to install). Bundles can also be passed on the command line or dropped onto the exe.

**From source:**

```bat
pip install -r requirements.txt
BNDLExplorer.pyw                      :: double click, or:
python -m bndlx path\to\A.BNDL [path\to\B.BNDL ...] [--select RESOURCE_ID]
```

Python 3.10+ (tested with 3.14). Built on [Dear ImGui](https://github.com/ocornut/imgui) through
[imgui-bundle](https://github.com/pthom/imgui_bundle).

**Building the exe:** `pip install pyinstaller`, then `python packaging\build_exe.py` → `dist\BNDLExplorer.exe`.

## The window

It is laid out like the Windows 11 File Explorer (dark theme by default; light theme under **… > Options**):

- **Tabs**: Home, folders, and one tab per open bundle. `+` opens a new tab (Ctrl+T), Ctrl+W closes one.
- **Address bar** with Back / Forward / Up / Refresh, clickable path segments (click the empty part to type a
  path) and a search box that filters the current view.
- **Command bar**: Open, Save, Cut, Copy, Paste, Rename, Delete, Import, Export, Replace, Convert, Sort, View
  and **…** (more: Save as, Extract all, Import from folder, Find, Go to id, Bundle properties, Options, Help).
- **Navigation pane**: Home, the open bundles with their resource types as folders, and pinned folders (add
  the game folder with **Add a folder**; folders open in the main view, bundles open with a double click).
- **Main view**: *Details* (Name = resource id, Type, Description, Size, Imports; sortable, resizable) or
  *Large icons* (textures show thumbnails). Multi-select with Ctrl / Shift, arrow keys, right-click menus.
- **Details pane** (right, toggled with the **Details** button): the selected resource with Preview, Info,
  Imports and Hex tabs.
- **Status bar**: item counts, selection size, messages, view switch.

## Resource names

Bundles store resources by number only. The **Name** column shows a name wherever one can be found; the **Id**
column keeps the number. **Find names** (Home page or **…**) scans the game folders of the navigation pane
once (about a minute) and keeps the result in `%APPDATA%\BNDLExplorer\names.json.gz`. Add the PS3 prototype
folder too if you have it: its debug data names many resources that the retail game shares.

*Exact names* (normal text) come from the game data:
- the debug name tables of bundles that have them (`gamedb://hawaii/World/.../Rock_01.mb?ID=...`, `trk_unit1_list`);
- 32-bit ids are the zlib CRC32 of the lower-case name, so any candidate can be checked exactly: strings in
  Genesys objects and widget JSON, Genesys type names, asset paths in the executable, and name patterns
  (`<script>.lua`, `<widget Name>_<object id>.json`, `<material>_<texture>[_fh][_fv]_renderable` for HUD
  quads, `<id>_VEHICLESOUND`, `TRK_UNIT<n>_GCVR`, the `_LOD<n>` renderables of every model path found, ...);
  candidates must also fit the resource type, which keeps random CRC matches out;
- Genesys type names (stored in the types).

*Worked-out names* (dimmer text; hover for how) come from the data around a resource:
- Genesys objects: their name field (`Name`, behaviour and widget names), else `<type> <number>`;
- widget JSON files: the `Name` inside them;
- vehicle bundles: the car name (from the damage behaviour object), the graphics spec (body, wheel 1-4 tyre /
  brake disc / rim / caliper) and the model's LOD table: `Porsche_911CarreraS_2012 wheel 1 tyre LOD0`;
- textures and materials: the model that uses them and the material slot: `Rock_01 Diffuse` (`(+n)` = also
  used by n other resources).

Numbers are not guessed: with CRC32, `crc(name + "_LOD0")` follows from `crc(name)`, so a wrong asset number
that happens to match a model id would also "match" its renderables; such names are never used.

## What it shows and edits

| Resource | Preview / editor | Export / import |
|---|---|---|
| Texture (RwRaster) | zoom, pan, mip levels, cube faces, R / G / B / A channels, sRGB flag | DDS (lossless, all mips), PNG; replace from PNG / DDS / TGA / JPG / BMP (BC1 / BC2 / BC3 / RGBA encoder, mip chain, format and sRGB choice) |
| GenesysObject | every field as a tree: numbers, vectors, colours, enums, flags, strings, references (click to jump) | .bres |
| GenesysType | schema: fields, types, offsets, flags; enum values | .bres |
| TextFile | text editor (JSON / XML) | .txt |
| LocalisedText (UI\LANGUAGE) | searchable string table, edit in place | CSV (`id,text`) for translations |
| ColourCube | 16³ grading cube as slices | 256 × 16 PNG |
| every type | imports (edit the ids, jump to the target), hex view with byte editing | .bres, raw chunks (.bin) |

Field names of Genesys data are hashes; short names are stored as text, a few are known, and any field can be
named (right click; the names are saved in `%APPDATA%\BNDLExplorer\labels.json`).

Other operations: **Rename** (change a resource id; imports of it in the bundle follow), duplicate with a new
id, delete, copy resources between bundles, find the resources that import a resource, find in all open
bundles, bundle properties (flags, root resource). **Undo / redo** (Ctrl+Z / Ctrl+Y) for every change.

## Drag and drop, copy and paste

- Drop `.BNDL` files on the window to open them.
- Drop (or copy in Explorer and paste with Ctrl+V) a PNG / DDS / TGA / JPG on a texture to replace it, a
  `.txt` on a text file, a `.csv` on a string table, a `.bres` on any resource, a `.bin` on a resource (it asks
  for the chunk). Files named `<resource id>.<ext>` replace those resources; `.bres` files add resources.
- Drag resources onto another bundle's tab or navigation entry, or Ctrl+C / Ctrl+X then Ctrl+V in another
  bundle, to copy / move them. PS3 ↔ PC conversion happens automatically where it is possible.
- Drag resources out of the window into Explorer, or Ctrl+C and paste in Explorer, to get them as files
  (textures as PNG or DDS, text as .txt, strings as .csv, others as .bres).

## Saving

Save (Ctrl+S) writes to a temporary file, reads it back and compares every resource before it replaces the
bundle; the first time a file is overwritten, the original is kept next to it as `<name>.orig`. The bundle
layout of the game is kept (compressed or not, alignment, header, debug data, trailing data): a bundle saved
without changes is **byte-identical** to the original (checked on all 5 671 retail PC and PS3 prototype
bundles). Bundles written by other tools (for example DGIorio's Blender exporter) keep their layout too.

## PS3 ↔ PC conversion

**Convert** saves the open bundle for the other platform. Converted: textures (DXT blocks copied, uncompressed
data (un)swizzled, RGBA16F byte-swapped, headers rewritten), Genesys types and objects (byte order of every
field, walked with the schema), colour cubes, text files and string tables. Meshes, materials, shaders and
other GPU data are platform-specific and are left out; the report lists them. Round trips PC → PS3 → PC and
PS3 → PC → PS3 give identical data (apart from a texture flag the PS3 header does not store).

The prototype and the retail game do not share every Genesys schema: converted data works in the other
build only where its types are the same there.

## Resource files (.bres)

One resource with all of its data: `BNDLRES1`, a JSON header (id, type, platform, stream, import table
position, alignment bits, sizes, debug name) and the four memory chunks as the game loads them. Importing a
.bres from the other platform converts it.

## File format notes

- bnd2 version 5, PC little-endian / PS3 big-endian. Compressed bundles store every chunk of every resource
  as its own zlib stream, back to back in entry order; uncompressed ones align each resource to the alignment
  in the top bits of its stored size. Imports live at the end of chunk 0.
- PC textures: 48-byte header + pixels in chunk 1 (mips largest first, padded to 128). PS3 textures: a
  CellGcmTexture header + pixels in chunk 2 (A8R8G8B8 Morton-swizzled; cube faces aligned to 128).
- Details: see the MW2012 knowledge base (docs on the bundle format, textures, Genesys).

## Tests

```bat
set BNDLX_PC=...\Need for Speed(TM) Most Wanted
set BNDLX_PS3=...\NPXX00207\USRDIR\HAWAII_MAIN
python tests\test_core.py                 :: library: saves, edits, conversion, strings
python tests\test_gui.py                  :: the real window: drops, copy between bundles, clipboard, undo, save
python tests\roundtrip_all.py "%BNDLX_PC%" "%BNDLX_PS3%"   :: every bundle saves byte-identical
```

`python -m bndlx FILES --screenshot out.png [--select ID]` renders the window, saves a screenshot and exits.

## Credits

Reverse engineering and tools: FrankHUN88453, with Claude. Dear ImGui by Omar Cornut; imgui-bundle by Pascal
Thomet. Icons: [Font Awesome Free](https://fontawesome.com) (CC BY 4.0), also used for the program icon.
