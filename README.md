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

Python 3.10+ (tested with 3.14); sounds need `soundfile` (libsndfile with mpg123 and LAME), the 3D view
`PyOpenGL`. Built on [Dear ImGui](https://github.com/ocornut/imgui) through
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
| VehicleList (VEHICLES\VEHICLELIST) | every car and manufacturer with names from the game's strings; edit any field, duplicate / delete / reorder cars | CSV (one row per car, then the manufacturers) |
| ColourCube | 16³ grading cube as slices | 256 × 16 PNG |
| Renderable, Model | 3D view: textured, lit, turn / move / zoom with the mouse, wireframe, LOD choice | glTF binary (.glb) or FBX with the diffuse textures; **Import FBX** writes edited geometry back |
| VehicleGraphicsSpec (VEH_*) | the whole car in 3D: body and the four wheels (tyre, disc, rim, caliper) at their places; LOD choice; wheel positions and scales editable (mirrored left / right) | glTF (.glb) of the assembled car; FBX export / **Import FBX** (body and wheels) |
| InstanceList (TRK_UNIT) | the whole track unit in 3D: every model instance in place, with its textures; collision over it | glTF (.glb) or FBX of the whole unit; Import FBX for the unit's own models |
| PolygonSoupList (TRK_UNIT) | collision in 3D, coloured by surface tag | glTF (.glb), FBX |
| ZoneList (HAWAII\PVS) | map of all 169 track units by district; click a zone to open its TRK_UNIT | |
| .SPS sound stream files | opened like a bundle with one sound: play, waveform, replace, save | WAV; replace from WAV / FLAC / OGG / MP3 / AIFF / .SPS; a whole folder as WAV |
| Material | shader, textures by slot with thumbnails, shader constants by name (editable colours / numbers); Go / Open for every texture | .bres |
| Wave (sound) | play / pause / stop with a play head on the waveform; click or drag on it to jump; time, channels, rate, length; stops when another item is selected | WAV; replace from WAV / FLAC / OGG / MP3 / AIFF (encoded as EALayer3) |
| GinsuEngineSound (car bundles) | engine rev sweep: RPM range, grains, play with the RPM at the play head, hold the engine at a chosen RPM | WAV |
| every type | imports (edit the ids, jump to the target, or open the bundle that has it), hex view with byte editing | .bres, raw chunks (.bin) |

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

## 3D models

Renderables (meshes) and Models (LOD tables of renderables) are shown in a 3D view (OpenGL, through PyOpenGL
in the window's own context). The vertex layout of a mesh comes from its material's shader (shader import at
0x8, VertexDescriptor import at 0x9C of the shader); shaders and many materials / textures are not in the
bundle itself, so they are also looked up in the game's global bundles (`SHADERS*.BNDL`,
`GLOBALMATERIALDICTIONARY.BNDL`, `GLOBALTEXTUREDICTIONARY.BNDL`, `VEHICLES\VEHICLETEX.BNDL`, ...) of the game
folder the bundle is in. Meshes show their Diffuse texture, or the material's diffuse colour constant; car paint
(chosen by the player in the game) shows as silver, glass as dark glass. Normals are the mesh's own where the
vertex format holds them in a readable form (plain vectors; PC cars: a tangent-frame quaternion whose z axis,
flipped where w < 0, is the normal; PS3: 11:11:10 `CMP` normals), else computed from the triangles.
Positions: float32 for the world and effects, s16 normalised × 10 m for vehicles. Index buffers are u16
triangle strips with 0xFFFF restarts (topology field in the mesh record; every retail mesh uses strips).

**Export glTF** writes a `.glb` (positions, normals, UVs, indices, diffuse textures as PNG, base colours).

**Export FBX** writes a binary FBX 7.4 (metres, Y up; Blender, 3ds Max, Maya, Unity) with every UV set,
the mesh's own normals, a Phong material per game material and the diffuse textures as PNG files in
`<name>_textures` next to it. Each object is named `R<renderable id>_<mesh index>`; further uses of the same
mesh (the other wheels) get `~1`, `~2`. An extra UV layer `bndlx_id` (leave it in place) keeps each vertex's
index in the game mesh.

**Import FBX** (in the 3D view, or Replace… with an `.fbx` on a Renderable, Model, VehicleGraphicsSpec or
InstanceList) writes an edited file back:

- every object named `R<id>_<n>` replaces that mesh; `~n` copies are skipped (they follow the first, e.g.
  edit the front left wheel); new parts must be joined into an existing object (in Blender: select the new
  part, then the object, Ctrl+J), since new meshes would need new materials;
- positions, all UV sets and normals are encoded in the mesh's own vertex format (tangent frames are turned
  with the normal); every other attribute (tangents, vertex colours, damage weights / zones, ...) is taken
  from the nearest original vertex. Vertices that kept their `bndlx_id` and place keep their exact bytes, so
  an unchanged round trip, even through Blender, leaves the data as it was;
- triangles are written as u16 triangle strips with 0xFFFF restarts, like every mesh of the game (at most
  65 535 vertices per mesh), the renderable's buffers are laid out again (PC: 32-byte aligned, index buffers
  padded to 16; PS3: 16-byte aligned) and its bounding sphere grows if needed;
- Ctrl+Z undoes it; Save writes the bundle (the original is kept as `.orig`).

The per-mesh bounds words (record 0x00-0x0F) are kept: centre = three s16 × 2^-14 (a scale code in the top
bits of the first word selects 2^-10 for large meshes), the extents are packed in a way not solved yet.

### Materials

A Material (same layout on PC and PS3) imports its shader (at 0x8), and per texture slot a texture and a
sampler state. Slots are 16-bit numbers (`0x0E88` Diffuse, `0x0D9C` Normal, `0x31F2` Specular, `0x2837`
Effects, ...). The material also holds shader constants, four floats each, keyed by a 32-bit hash: the hash is
the inverted CRC32 of the constant's name in the shader (`~crc32("PbrMaterialDiffuseColour")` =
`0x067923B3`), so the names are read from the game's own shaders and every constant of the game is named.
Constants can be edited in place (drag or type the numbers). Every texture has **Go** (select it, when its
bundle is open) or **Open** (open the bundle that has it: **Find names** also records where each resource
lives).

## Track units (the world)

`HAWAII\TRK_UNIT<n>.BNDL` files are the pieces of the city. Selecting a unit's **InstanceList** shows the
whole piece in 3D: every model instance at its place, together with the unit's props, dynamic objects (signs,
...) and compound objects (street lamps, ...), with textures (tree leaves and fences cut out by their alpha).
**Neighbours** adds the units that share a border with it (from `HAWAII\PVS.BNDL`).

`HAWAII\PVS.BNDL` holds the **ZoneList**: its preview is a map of the city, one polygon per track unit,
coloured by district. Hover a zone for its unit, district and neighbours; click it to open the unit in 3D
(wheel zooms, right drag moves). Models shared between units come from `HAWAII\DISTRICT_*.BNDL` and `HAWAII\GLOBALRESOURCES.BNDL`;
they are found automatically (faster, and complete, after **Find names**). The list above the view switches
between **World**, **Collision** and **World + collision** (collision as a coloured wireframe), and the LOD
list shows the lower detail levels of every model. **Export glTF** writes the whole unit.

The **PolygonSoupList** of a unit is its collision: shown in 3D with one colour per collision tag (road, kerbs,
buildings, terrain, invisible walls, ...); hover the summary line for the tags and their triangle counts.

Roads and ground use blend shaders without a Diffuse slot; the view uses their first colour layer (roads:
the asphalt colour), so ground looks plainer than in the game.

The PS3 prototype's `HAWAII` track units are empty: its playable world is **`SEACREST`** (Seacrest County, 215
track units in 5 districts, 1.5 GB). Its `PVS.BNDL` map, its units in 3D (with Neighbours and collision) and its
older list layouts all work the same way.

## Sound stream files (.SPS)

Music, ambience, sequence sound tracks, video sound tracks and some speech are not in bundles but in `.SPS`
files (`UI\SONGS`, `SOUND\STREAMS`, `UI\SEQUENCES\STREAMS`, `UI\MOVIES`, `EN_US\STREAMS`). They hold nothing
but EALayer3 audio. Open one (double click in a folder, **Open**, or drop it on the window) and it shows as a
tab with one sound: waveform, **Play**, **Export WAV**, **Replace** (or drop a WAV / FLAC / OGG / MP3 / AIFF on
it), undo, and **Save** writes the .SPS again (the original is kept as `.orig`). Songs are named after their
artist and title (from `UI\SONGS\SONGS.BNDL`), also in the folder view.

Files that start without a header continue a sound whose first second is stored in a bundle (the Wave with
id `0x01000000_<file number>`). That part is found automatically, so these play whole too; **Open that
bundle** jumps to it, and replacing such a sound is done there (the bundle and the .SPS file are rewritten).

**... > Export sound streams (.SPS) of a folder as WAV** converts every .SPS file of a folder and its sub
folders (for example the whole soundtrack: `UI\SONGS`, named `Artist - Title (id).wav`). An .SPS file can also
be used as the source when replacing any sound.

## Soundtrack editor

**... > Soundtrack editor (songs and playlists)** opens the game's music (PC): every song of
`UI\SONGS\SONGS.BNDL` with its artist, title, length and playlists.

- **Add songs...**: pick WAV / FLAC / OGG / MP3 / AIFF (or .SPS) files; they are encoded like the game's songs
  (EALayer3, stereo) and added to the two soundtrack playlists (or to the playlist shown). File names like
  `Artist - Title.mp3` give the artist and title.
- Edit **artist** and **title** in place (they apply as you type; a name shared by several songs is changed for
  this song only). A red mark shows songs in a soundtrack playlist without an artist or title - the game shows
  those as "0" - and Save asks before writing them (the untitled menu tracks have no names at all), **Replace audio...**, **remove** a song, play it, tick the playlists (the two checkboxes are the two
  soundtrack lists; **Lists...** shows all 13), reorder a playlist (choose it in the list at the top).
- **Save** writes `UI\SONGS\SONGS.BNDL`, the artist / title strings into every `UI\LANGUAGE\*.BNDL` (kept sorted
  by id, as the game expects) and the new `UI\SONGS\<id>.SPS` files; every changed file is kept once as `.orig`.

How it is stored: a Song object points at a Wave that streams `UI\SONGS\<id>.SPS` and holds the string ids of
its artist and title; a SongList holds references to Songs. The two 42-song lists (one referenced by
GAMEMODES) contain the whole licensed soundtrack; the 12-song list is also used by GAMEMODES, the one-song lists
by scripted sequences. New objects get ids from 0x0FA00000 up, far from the game's own. Whether a list plays
in races, pursuits or menus is not known yet: test in the game.

## License plates

Every car's number plate shows the text of its content pack: `NEED4SPD` (base game), `ULTIMATE`, `VELOCITY`,
`MOVILGND` (Movie Legends), `NFS HERO` (NFS Heroes pack, e.g. the BMW M3 GTR). The plate is 8 letter quads whose
UVs point at the cells of a font atlas (`VEHICLETEX.BNDL`, texture `0x0100000000138805`: A-Z, `-`, 1-9); the
letters come from an 8-character text that NFS13.exe holds in a table of five 12-byte slots, copied about fifty
times into its `.rdata`. **... > License plates...** shows the five texts with a preview drawn in the game's plate
font; type new ones (up to 8 characters: A-Z, 1-9, `-`, space; `0` is shown as `O`) and **Write the plate texts**
changes every copy in NFS13.exe (kept once as `NFS13.exe.orig`; **Restore the original exe** puts it back).

The player's own plate text ("registration") has its own editor in the game (Easydrive > EDIT LICENSE PLATE > REGISTRATION,
typed with the keyboard), unlocked at Speed Level 15 in multiplayer. The garage menu shows one of two item
lists depending on `Players.LocalPlayer.CustomiseLicensePlateAvailable`; in the locked list the REGISTRATION item
is inactive. **... > License plate text (registration) editing...** copies the unlocked list's items into the
locked one (`PlateLockedItems.json` in `UI\SCREENS2\1347319.BNDL` and `264716.BNDL`), so the editor is offered
right away; **Restore** puts the game's own list back from the `.orig` files.

## Vehicle list

`VEHICLES\VEHICLELIST.BNDL` holds the list of every car of the game (PC: 110 rows, PS3 prototype: 188) and of
the manufacturers. The preview shows them as a table with the names from the game's own strings
(`UI\LANGUAGE\0001.BNDL`); select a car to edit its fields: name / description string ids, manufacturer
(a list), images, content pack, top speed, 0-60 time, power, redline, year, the six 0-10 ratings of the car
select screen, flags (1 police, 8 traffic, 18 player cars). Image, sound and Genesys fields link to their
resources (**Go** / **Open**); the vehicle id opens the car's `VEH_<id>` bundle. **Duplicate**, **Delete**
and the arrows change the rows. The PC retail list (version 1017) and the PS3 prototype list (1016) have
different layouts; both are supported.

**Export CSV** writes one row per car (plus a read-only `(name)` column), a blank line, then the
manufacturers; **Import CSV** (or dropping the CSV on the resource) replaces the list with the file's rows, so
cars can be added, removed and reordered in a spreadsheet.

CSV files from Excel work with any regional setting: `;` separators and decimal commas (Hungarian, German,
... Excel) are recognised. String table CSVs write their ids as `0x000185E8` so that Excel keeps them as text
(it would turn `000185E8` into a number).

## Sounds

### Engine sounds (Ginsu)

Type 0x80 is EA's granular engine synthesis, **Ginsu**: every car bundle (PC `VEH_*_HI`, PS3 `VEH_*_EN`) has
several, each one recording of the engine sweeping through its revs (on-load: accelerating; off-load: engine
braking) cut into grains of one engine cycle each; the game plays the grains of the current RPM. The header
(`Gnsu30` PC / `Gnsu20` PS3) gives the RPM range, 51 RPM steps (sample position of each), the grain start
positions and the sample rate; the audio is EA-XAS v0 (19-byte frames of 32 mono samples). BNDL Explorer
shows the RPM range and grains, plays the sweep (the RPM at the play position is shown), exports WAV, and
previews the engine held at a chosen RPM.

### Waves

Every other sound of the game (PC and PS3) is an EA **SPS** stream with the **EALayer3 v1** codec (MP3 based):
blocks `H` (SNR header: codec, channels, sample rate, samples), `D` (EALayer3 frames), `E` (end). An EALayer3
frame is one MPEG Layer III granule of a mono or stereo stream; 6-channel sounds are three stereo streams whose
frames alternate. Decoding rebuilds standard MP3 frames (bit reservoir) and decodes them with libsndfile
(mpg123); the first MPEG frame (1152 samples of encoder / decoder delay) is dropped and the 47 samples stored as
PCM in the second granule take its place, which is how the block sample counts of every game file add up.
Importing encodes with LAME, takes the granules apart again and writes the same structure. All 15 000 sounds
of both builds decode to their exact length; encoded sounds come back at 45-69 dB signal to noise.

Wave resources come in three kinds (field 0x20 of the 0x80-byte header): the whole sound in the bundle; a
reference to a stream file (`UI\MOVIES\2026359.SPS`); or a prefetched stream: the first second in the bundle,
the rest in `<GameChanger id>.SPS` somewhere in the game folder (`UI\SEQUENCES\STREAMS`, `SOUND\STREAMS`,
`EN_US\STREAMS`, ...). BNDL Explorer plays and replaces all three; stream files it rewrites are kept as `.orig`
first. Sounds of languages that are not installed have no stream files: only their start can be played.

## Saving

Save (Ctrl+S) writes to a temporary file, reads it back and compares every resource before it replaces the
bundle; the first time a file is overwritten, the original is kept next to it as `<name>.orig`. The bundle
layout of the game is kept (compressed or not, alignment, header, debug data, trailing data): a bundle saved
without changes is **byte-identical** to the original (checked on all 5 671 retail PC and PS3 prototype
bundles). Bundles written by other tools (for example DGIorio's Blender exporter) keep their layout too.

## PS3 ↔ PC conversion

**Convert** saves the open bundle for the other platform. Converted: textures (DXT blocks copied, uncompressed
data (un)swizzled, RGBA16F byte-swapped, headers rewritten), Genesys types and objects (byte order of every
field, walked with the schema), colour cubes, text files, string tables and sounds (the audio stream is the same on both). Meshes, materials, shaders and
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
python tests\test_audio_gui.py            :: sound preview: jump to a position, stop when another item is selected (silent)
python tests\test_fbx_gui.py              :: FBX export with textures, import back, an edited object, undo
python tests\roundtrip_all.py "%BNDLX_PC%" "%BNDLX_PS3%"   :: every bundle saves byte-identical
```

`python -m bndlx FILES --screenshot out.png [--select ID]` renders the window, saves a screenshot and exits.

## Credits

Reverse engineering and tools: FrankHUN88453, with Claude. Dear ImGui by Omar Cornut; imgui-bundle by Pascal
Thomet. Icons: [Font Awesome Free](https://fontawesome.com) (CC BY 4.0), also used for the program icon.
