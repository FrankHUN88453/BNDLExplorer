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

The app opens with a 3.2-second rounded splash screen featuring the BNDL logo, then fades the dashboard in once. The dashboard interface is
the only launchable UI, with selectable Violet, Blue, and White-glow themes. All keep
near-black surfaces and subtle borders; the upper-left light and panel-frame highlights follow the selected accent:

- **Workspace sidebar**: a branded header, grouped Home/library/folder navigation, expandable bundle resource
  categories, and an Open bundle action. Collapse it to an icon rail; bundle icons open a resource-category flyout.
- **Workspaces**: the workspace bar keeps Bundle Explorer separate from the **EXPERIMENTAL** workspace. The
  original Font Awesome toolbar and resource-type icons are restored.
- **Resizable panes**: drag the visible handles between dashboard and its inspector or Hex Editor panes to adjust their widths.
- **Hex Editor**: lazily maps arbitrary files, stages byte and printable ASCII edits (including text insertions and deletions) until Save, and provides insert/overwrite,
  undo/redo, hex/text/integer/float search, replace, file comparison, 8/16-bit checksums, CRC32, configurable
  CRC-16, MD5/SHA-1/SHA-512, byte statistics, and a multi-format data inspector. The Hex Editor lives in the
  **EXPERIMENTAL** workspace while it is being developed. View options include Windows
  ANSI, OEM, Macintosh, and EBCDIC text encodings; hexadecimal/decimal/octal offsets; selectable hex/text
  columns; byte grouping; configurable bytes per row; and fit-to-window. **Find all** lists matches in the
  search-results pane. The **View options** menu can show or hide save, edit, and compare/analysis toolbar
  actions. **Ctrl+Alt+D** toggles the data inspector, **Ctrl+Alt+C** calculates checksums,
  **Ctrl+Alt+S** toggles search results, and **Alt+F7** cycles the tool pane. Open a file with **Ctrl+O** or
  use **Open file**; save with **Ctrl+S**.
  This workspace edits regular files only; it does not access raw disks or live process memory.
- **Resource browser**: search and filter resources, with name, id, type, and size in a sortable table; click a column header to sort by it.
- **Inspector**: the selected resource's preview, editable fields, folder view, imports, and hex data.
- **Actions**: open, save, import, export, replace, delete, and additional bundle tools under **More**.
- **Recent files**: reopen bundles from the dashboard home view.
- **Compare bundles**: open two `.BNDL` files with the same filename (from different folders), then choose
  **More > Compare same-name bundle** to view resource, metadata, chunk, and decoded Genesys field differences.

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
| Texture (RwRaster) | zoom, pan, mip levels, cube faces, R / G / B / A channels, sRGB flag; **Full size** (or double click): the texture at its full resolution in a window over the whole app (1:1, 2:1, 4:1, fit, pixel values); Replace / Export right in the preview | DDS (lossless, all mips), PNG; replace from PNG / DDS / TGA / JPG / BMP (any size; BC1 / BC2 / BC3 / RGBA encoder, mip chain, format and sRGB choice), or drop an image on the preview |
| GenesysObject | **Fields** view for editable values; **Folder** view shows nested structs and arrays as folders, text fields as editable / exportable text files, and referenced resources as clickable files; **Inference** generates source-like pseudocode and heuristic PC executable assembly candidates | .bres |
| GenesysType | **Fields** schema table; **Folder** view with struct/base folders, enum values, field metadata, and links to referenced types; **Inference** generates schema pseudocode and heuristic PC executable assembly candidates | .bres |
| TextFile | text editor (JSON / XML) | .txt |
| LuaCode | source editor for plain-text Lua; apply changes, save with the bundle, export / replace as `.lua`; binary payloads stay available as `.bres` / raw chunks | `.lua` source or `.bres` |
| LocalisedText (UI\LANGUAGE) | searchable string table, edit in place | CSV (`id,text`) for translations |
| VehicleList (VEHICLES\VEHICLELIST) | every car and manufacturer with names from the game's strings; edit any field, duplicate / delete / reorder cars | CSV (one row per car, then the manufacturers) |
| ColourCube | 16³ grading cube as slices | 256 × 16 PNG |
| Renderable, Model | 3D view: textured, lit, turn / move / zoom with the mouse, wireframe, LOD choice | glTF binary (.glb) or FBX with the diffuse textures; **Import FBX** writes edited geometry back |
| VehicleGraphicsSpec (VEH_*) | the whole car in 3D: body and the four wheels (tyre, disc, rim, caliper) at their places; LOD choice; wheel positions and scales editable (mirrored left / right) | glTF (.glb) of the assembled car; FBX export / **Import FBX** (body and wheels) |
| InstanceList (TRK_UNIT) | the whole track unit in 3D: every model instance in place, with its textures; collision over it | glTF (.glb) or FBX of the whole unit; Import FBX for the unit's own models |
| PolygonSoupList (TRK_UNIT) | collision in 3D, coloured by surface tag | glTF (.glb), FBX |
| ZoneList (HAWAII\PVS) | map of all 169 track units by district; click a zone to open its TRK_UNIT | |
| TrafficData | traffic lane hulls, section boundaries and lane points in an interactive X-Z map; zoom and pan | |
| HSMData | state definitions and entry / exit transitions, data definitions, and behaviour interfaces; filter and export a text report | |
| Event / gameplay reference viewer (**…**) | typed event records from the event Genesys resource; editable event scalar fields and primitive arrays; checkpoint routes with an editable route list; gameplay and trigger locations, AI player types, cop types, and rollouts from the visualizer JSON datasets | custom checkpoint route as JSON |
| .SPS sound stream files | opened like a bundle with one sound: play, waveform, replace, save | WAV; replace from WAV / FLAC / OGG / MP3 / AIFF / .SPS; a whole folder as WAV |
| Material | shader, textures by slot with thumbnails, shader constants by name (editable colours / numbers); Go / Open for every texture | .bres |
| Wave (sound) | play / pause / stop with a play head on the waveform; click or drag on it to jump; time, channels, rate, length; stops when another item is selected | WAV; replace from WAV / FLAC / OGG / MP3 / AIFF (encoded as EALayer3) |
| AnimationList, Animation (PC and PS3 prototype) | a car's damage animations played **on the car** (body skinned to the damage skeleton): bumpers fall off, bonnets pop up, doors swing; other animations on their skeleton with bone paths (camera shots); play / pause, time slider, loop, speed | FBX: bones, the car skinned, every animation as a take |
| Skeleton | the bones on their car (or alone), with a table: parent, position, name hash | |
| ControlMesh (PS3 prototype cars) | the crash deformation lattice: up to 64 points on the body over the car, coloured by how deep each can dent, with the direction it moves; **Dent** crushes the car with it; the car view has a **Control points** switch | max dent per point editable; glTF / FBX |
| GinsuEngineSound (car bundles) | engine rev sweep: RPM range, grains, play with the RPM at the play head, hold the engine at a chosen RPM | WAV; replace from a recording (WAV / FLAC / OGG / MP3): RPM tracked from its pitch or a steady sweep |
| every type | imports (edit the ids, jump to the target, or open the bundle that has it), hex view with byte editing | .bres, raw chunks (.bin) |

Genesys **Inference** does not recover or execute original source: its pseudocode is generated from the schema and
serialized object values. Assembly candidates come only from statically reachable x86 code in the PC `NFS13.exe`,
matched by field offsets; they are explicitly low-confidence and may be unrelated. The generated text is an
exportable explanation, not a replacement format for editing the bundle.

Field and enum value names of Genesys data are stored as hashes. The hash is known (found in the PS3
prototype's debug build, `rw::core::stdc::CalculateCrc32`): an MSB-first CRC-32 (polynomial 0x04C11DB7) whose
register starts as the complement of the name's first four bytes, result complemented, so names of four
characters or fewer hash to themselves (`Name`, `Tint`). The names themselves are not stored, but **Find names**
recovers them: every identifier of NFS13.exe, of the PS3 debug SELF (when the prototype folder is scanned too)
and of the bundles' objects, scripts and text, and every run of words inside them (`GetMaxDisplacement` ->
`MaxDisplacement`), is hashed and matched against the fields of every type; a hash that several candidates match
takes the one sharing the most words with its type and its sibling fields, array counts are tried as
`<array>Count`, and pairs of words from found names fill in more. Retail PC, all types: about half of the fields
in use get their real name from the PC files alone, three quarters with the PS3 debug build (3859 of 6880 field
hashes); chance matches are rare (~0.1 % of single words, ~2 % of pairs). Hover a field for where its name comes
from; any field can also be named by hand (right click; saved in `%APPDATA%\BNDLExplorer\labels.json`), and the
rename box tells whether a typed name is the real one (its hash matches).

BNDL Explorer also includes 2,590 hash-verified field and enum names imported from the TypeScript tools'
`foundHashes.txt` maps, plus their event and car id tables. User labels and names found by scanning game files
still take precedence; hash collisions retain their alternative candidates in the field tooltip. To refresh the
bundled map from a MostWanted TypeScript checkout, run
`python tools\import_ts_names.py path\to\MostWanted`.

Other operations: **Rename** (change a resource id; imports of it in the bundle follow), duplicate with a new
id, delete, copy resources between bundles, find the resources that import a resource, find in all open
bundles, bundle properties (flags, root resource). **Undo / redo** (Ctrl+Z / Ctrl+Y) for every change.

## Drag and drop, copy and paste

- Drop `.BNDL` files on the window to open them.
- Drop (or copy in Explorer and paste with Ctrl+V) a PNG / DDS / TGA / JPG on a texture to replace it, a
  `.txt` on a text file, a `.lua` on a plain-text LuaCode resource, a `.csv` on a string table, a `.bres` on any resource, a `.bin` on a resource (it asks
  for the chunk). Files named `<resource id>.<ext>` replace those resources; `.bres` files add resources.
- Drag resources onto another bundle's tab or navigation entry, or Ctrl+C / Ctrl+X then Ctrl+V in another
  bundle, to copy / move them. PS3 ↔ PC conversion happens automatically where it is possible.
- Drag resources out of the window into Explorer, or Ctrl+C and paste in Explorer, to get them as files
  (textures as PNG or DDS, text as .txt, LuaCode source as .lua, strings as .csv, others as .bres).

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

**Shaded** (on by default) draws the materials the way the game's physically based shaders use them:
albedo (car paint, chosen with the colour button, mixed under the livery by the diffuse alpha), the normal map
(RGB tangent-space normal in the DirectX convention: the stored bitangents of the cars point to -v, verified;
A = roughness), the specular map (vehicles: RGB F0; world shaders pack it: R reflectance, G gloss,
and the ColouredSpecular ones are RGB), vertex ambient occlusion, a key light, sky / ground ambient and
reflections, GGX with Fresnel and a clear coat on paint. The world's packing is read from the data and
approximate; tyres have no colour map (the rubber colour is in the shader). Off: the texture under a head light.

Which channels a car shader reads comes from the game's own pixel programs (their DXBC, traced channel by
channel): the car shaders have **no metalness** (metal parts are a dark albedo with a bright specular colour), so
the specular map's A is unused, except on the textured `Alpha` badge / wheel shaders (a mirror coat: 1 reflects
like a mirror) and `Wheel_Alpha1bit_Normalmap` (roughness; its cut-out is the normal map's A). The cabin / badge
`..._NormalMap_Emissive_...` shader reads only G of its "normal" map (roughness, no bumps), `Wheel_Textured_Roughness`
only its A, the `Lightmap` ones only the normal. (Earlier versions took specular A as metalness everywhere: badges
such as the M3 GTR's BMW roundels and the cabins that share an 8x8 default map with A = 1 turned black.)

Glass and lamps: `Glass` / `Refraction` shaders are see-through (their `OpacityMin` share of their own colour,
0.09 for a windscreen, plus Fresnel reflections; drawn after everything else, far to near), `Glass_Colourise`
tints what is behind it (`MaterialColour_SimpleMultiply`, the red tail-light glass), so cabins, headlight
reflectors and projectors show through. **Lights** and **Brake** light the `LightmapLights` masks: every
channel in its material's `LightmappedLights{Red,Green,Blue,Alpha}ChannelColour` times
`mSelfIlluminationMultiplier` (R brake, G running / plate lights, B headlights, A tail lights; a channel without
a colour stays dark): `Emissive` shaders glow (UV set 0), `Lightmap` shaders are lit by the lamps (UV set 3).
`Alpha1bit` shaders cut out by the alpha test (the wheels' crest).

**Textures...** lists every texture the model uses (colour, normal, specular, light maps) with its size: View
opens it at full size, Replace swaps it (the 3D view shows the new one at once; Ctrl+Z undoes, Save keeps it).
Textures in bundles that are not open (`VEHICLETEX`, ...) get an Open button first. The Material view has View /
Replace next to each texture slot too.

**Export glTF** writes a `.glb` (positions, normals, UVs, indices, diffuse textures as PNG, base colours).

**Export FBX** writes a binary FBX 7.4 (metres, Y up; Blender, 3ds Max, Maya, Unity) with every UV set,
the mesh's own normals and a material per game material with its maps as PNG files in `<name>_textures`:
base colour (the paint under the livery, the specular colour on metal), normal map, roughness and metalness
(Blender's metallic where the specular colour is metal-bright, or a badge's mirror coat), linked as DiffuseColor /
NormalMap / ShininessExponent / ReflectionFactor, which Blender turns into a Principled material with those
textures; the base colour keeps its alpha only where the shader cuts out or blends by it. Glass gets its opacity (Blender's alpha), tinting glass its colour, and with Lights
on, the glowing lamp masks are baked into an emission map (EmissiveColor / EmissiveFactor). Each object is named `R<renderable id>_<mesh index>`; further uses of the same
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

### Animations

Retail PC animations (542 skeletons, 1297 animations and 379 animation lists, all read):

- **Skeleton** (type 0xB2): `u16 version 2, u16 bones, u32 bone offset, u32 id offset`; per bone (0x30
  bytes) the bind position and rotation (quaternion x y z w) in model space, then parent, previous sibling,
  last child and own index; u32 name hashes.
- **Animation** (0xB3): a 0x60-byte header (keys, bones, rotation / translation track counts, keys per
  second at 0x30, rotation codec at 0x34, nine offsets from 0x38): a translation track for bone 0, the other
  translation tracks, per-bone translation and rotation track indices (0xFF = none), and the rotation keys.
  Translations are f32x4; rotations f32 quaternions or 32-bit **smallest three** (bits 30-31 the largest
  component, three 10-bit components in x y z w order, ±1/√2). Keys are stored key after key (all tracks of a
  key together) and are **added to the bind pose**: the translation to the bone's local position, the
  rotation after its local rotation.
- **AnimationList** (0xB0): the animations stored inside, with start / middle / end sound slots.

Every car has an AnimationList of 8 damage animations used by its `DamageBehaviour`: 21 keys from whole to
wrecked for one side of the car (front, rear, left, right, ...). Car body vertices are skinned to the damage
skeleton (BLENDINDICES / BLENDWEIGHT), so BNDL Explorer plays them on the car; parts that come off (bumper
covers, mirrors) are moved out of sight by their bones. The `EN_US` `FEEDBACKGROUPS` animations are camera
shots in world coordinates and car rigs (body and four spinning wheels).

**PS3 prototype** animations (731, all read) use an older layout: no version, a 0x40-byte header (keys, bones,
translation tracks, rotation and translation bytes per key, size, keys per second at 0x20, codec at 0x24, six
offsets at 0x28), every bone has a rotation track, per-bone scale; inside lists the offsets are zero (filled
in when loading) and follow from the counts. Prototype car vertices carry two skeleton bones (attribute 7 / 1,
slots 0-1; no weight = the root) and two ControlMesh points (slots 2-3): the damage animations move the body
through the bones, and the ControlMesh view's **Dent** moves every vertex along its points' directions.

**Export FBX** (animation view, or Export > As FBX with skeleton and animation) writes the bones as LimbNodes,
the car skinned to them (one cluster per bone, bind pose) and every animation of the resource as a take of local
translation / rotation curves. Checked in Blender: an armature with one action per animation, bones and
deformed vertices where BNDL Explorer puts them (0.0 mm apart).

### ControlMesh (PS3 prototype)

The prototype's cars have a crash deformation lattice, resource type 0x210, imported by the
VehicleGraphicsSpec at 0x8: `u32 version 1, u32 size, 0, 0`, then 64 positions on the car's body, 64 unit
directions (the way each point moves when hit, mostly inward) and 64 maximum displacements in metres (vec4s,
the displacement repeated four times; rigid points hold 0 or about 1e-8). Cars with fewer points leave the
remaining slots all zero. The retail game has no such resources and no slot for them (its cars dent through
the damage skeleton alone); the `Genesys.Gen.ControlMesh` tuning object of the same name is still in every
retail car. BNDL Explorer draws the points over the car and edits the max dent per point.

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

## Triggers (TRIGGERS.BNDL)

`HAWAII\TRIGGERS.BNDL` holds the **TriggerData**: every trigger shape of the world (PC: 6404 boxes, 53 spheres,
2585 locators). Its preview is a map, best opened with **Open the trigger map in a large window**: the triggers
from above over the districts (`PVS.BNDL`) and the traffic lanes (`TRAFFIC.BNDL`) of the same folder, coloured by
trigger type.

- The list on the left shows / hides trigger types and shapes; the box finds every trigger of one owner id.
- Click a trigger to select it, drag it to move it; the panel on the right edits its type, flags, owner id,
  position, half size, heading (or the full rotation), radius or direction, and lists the triggers of the same
  owner.
- **Add box / sphere / locator** (at the centre of the view, copying the selected trigger of that shape),
  **Duplicate**, **Delete**; **Export CSV / Import CSV** for bulk edits (`;`, decimal commas allowed).
- Every change is one undo step. The cell grid the game searches (45 x 39 cells of 250 m) is rebuilt from the
  shapes the way the game's own tool built it (an unchanged file is written back byte for byte).

Trigger types are numbers; the names in the list come from the gameplay data that refers to the triggers'
owners (events, jack spots, roadblocks, drift markers, safehouses, collectables, ...); hover a type for where it
is used. Types without a name are not referred to by any data (the game looks them up by number); `?` marks a
guess.

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

**Replace with a recording** (or Replace / drop an audio file on it) makes a new engine sound from a WAV / FLAC /
OGG / MP3 of an engine sweeping through its revs (a dyno pull, a free rev): rising for an on-load sound, falling
for an off-load one. The RPM through the recording is either followed from its pitch (a four-stroke engine fires
cylinders / 2 times a revolution: give the recorded engine's cylinders and the RPM it starts at; **Analyse**
plots the curve found) or taken as a steady sweep between two RPMs. The recording is resampled to the sound's
rate, cut into grains of one engine cycle (120 / RPM s), the 51 RPM steps placed where the sweep passes them (the
lowest at sample 721 at the earliest, like every retail sound) and encoded as EA-XAS v0 (4-bit ADPCM, ~45 dB).
Undo with Ctrl+Z; Save writes the bundle (the original is kept as .orig).

The pitch tracking follows the autocorrelation peak of the firing period from the start RPM, only rising (or
falling) and at most ~3 % every 10 ms, and carries the sweep on at its recent rate where the pitch fades. On the
game's own 60 sweeps with the right cylinder count it finds the RPM to within 1-2 % on most rising sweeps; where
no clear pitch shows on more than 30 % of the recording it warns (other cylinders or start RPM, or a steady
sweep).

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

### Port into a game bundle (scan the game first)

**Convert** writes a new bundle with only the resources it could convert, so it is not a bundle the game can
load: the engine asks for every resource it knows, and a converted Genesys object is only readable when the
game's own schema has the layout it was written with. **Port** is for that case. It has its own window
(`... > Port into a PC game bundle...`; the Convert result offers a button for it too), in three steps:

1. **Source bundle**: the PS3 bundle with the data you want (a PS3 bundle that is open is filled in for you).
2. **Game bundle**: the PC game's bundle of the same name. The game folder around it is scanned.
3. **Save the result as**: a new file. The game bundle itself is never changed.

**Scan the game and preview** then lists exactly what will be taken from the source and what stays as the game
has it, with the reason for each (filter and search the table). Nothing is written until you press **Write the
ported bundle**; it also saves a per-resource report next to the result (`<file>.port.txt`). The checkbox under
step 3 turns on the float guess described below. From the command line:

```bat
python -m bndlx port SOURCE_PS3.BNDL GAME_PC.BNDL OUT.BNDL [--lenient] [--dry-run] [--game DIR] [--source-game DIR]
```

The game folder is scanned first (every bundle once: all Genesys types and every resource id; cached per folder,
the PS3 folder is scanned too when it is found). Then the result is the game's own bundle with the same ids,
types, order and count (nothing is added or removed), and only resources that are safe are taken from the source:

| Resource | Taken from the source when |
|---|---|
| Texture | both are the same kind (2D / cube map); the game's own header flags are kept |
| ColourCube, TextFile, LocalisedText, Wave | always (converted) |
| GenesysObject | its type and everything the type uses are identical in both builds (id, size, every field); it converts cleanly, reads back with the game's schema, converts back to the same bytes, and every resource it imports exists in the game |
| GenesysType | never: the game's own schema is kept |
| everything else (materials, shaders, vertex layouts, meshes, ...) | never: platform-specific, the game's own is kept |

Everything not taken stays exactly as the game has it. A report (also written next to the output as
`<file>.port.txt`) lists what was converted, what was kept and why, and what exists only in the source.
`--lenient` (the checkbox in the window) also converts Genesys objects whose schema leaves some data unexplained when
that data is clearly float32 (for example the colour tables of `EnvironmentKeyframe`); it is a guess, so the
report counts those objects.

Fixed with this feature: a layout bug that made valid `EnvironmentKeyframe` objects fail to convert (an array
that points into its own struct, with a 16-bit count on one of its floats, was reported as a conflict between
two fields; a declared field now wins over an array element laid over it).

## Resource files (.bres)

One resource with all of its data: `BNDLRES1`, a JSON header (id, type, platform, stream, import table
position, alignment bits, sizes, debug name) and the four memory chunks as the game loads them. Importing a
.bres from the other platform converts it.

## Mod Launcher (mod packages)

A separate program in the same repository: `ModLauncher.exe` (`python packaging\build_launcher.py`), or
`python -m bndlx.launcher [game folder]`. Put it into the game folder (it opens that folder) or anywhere (choose
the folder; any drive). BNDL Explorer itself has no mod part.

**Load order tab.** Mod packages (.zip) in a list, the later ones win: add them with **Add packages...** or drop
them on the window, tick / untick, drag rows (or Top / Up / Down / Bottom) to reorder.
- **Apply** builds every file a ticked package touches from its original (`<file>.orig`, kept once the first time
  the file is changed) with the packages applied in list order. Bundles are merged per resource: two packages only
  meet where they change the same resource (or the same whole file).
- When two packages meet, Apply lists the elements they share (file and resource) and asks which one is to win
  (load later); the answer reorders the list and is not asked again (**Ask me again for every pair** in the
  Conflicts tab). The Conflicts tab shows every shared element and who wins it.
- Files no ticked package touches any more go back to their original, files the packages created are removed.
  A file changed outside the launcher (an edit saved in BNDL Explorer that is not in a package) is not
  overwritten unless you say so.
- **Apply and start the game** (through Steam when `steam_appid.txt` is there, else `NFS13.exe`; keep the EA app
  offline), **Restore the original game** (every file back, the list stays), **Export order... / Import
  order...** (`mod_order.json`: names, file names and md5s, ticks; an import finds the packages by md5 or file
  name next to the order file and in the folders of the listed packages).
- The list lives in `<game>\bndlx_mods\profile.json`.

**Make a package tab.** Choose a game folder with your changes, **Find the changed files** lists every file that
differs from its `.orig` (bundles with the number of changed resources; executables unticked), give a name /
author / version / description and **Write the package...**.

**What is in a package** (an example: a moved jack spot, a changed HUD screen object, a text file):

```
My mod.zip
  manifest.json
  bundles/HAWAII/TRIGGERS.BNDL/00000000e4a32837_0.bres       changed resources of a bundle, one .bres each
  bundles/UI/SCREENS2/371621.BNDL/0000000021af9147_0.bres    (only the 1 changed resource of 1297)
  files/VERSION.TXT                                         any other file, whole
```

```json
{
 "format": "bndlx-mod", "version": 1,
 "name": "My mod", "author": "Frank", "mod_version": "1.0",
 "description": "Moves a jack spot, tweaks a HUD screen.",
 "platform": "PC", "created": "2026-11-03T07:23:28", "tool": "Mod Launcher 0.1",
 "files": [
  {"path": "HAWAII/TRIGGERS.BNDL", "kind": "bundle", "size": 280972,
   "base_md5": "eba8c9...", "result_md5": "f39ecf...",
   "resources": [{"key": "0xe4a32837/0", "type": 514, "name": "", "md5": "6d1350..."}], "removed": []},
  {"path": "UI/SCREENS2/371621.BNDL", "kind": "bundle", "size": 1117568,
   "base_md5": "2eb643...", "result_md5": "aaacdf...",
   "resources": [{"key": "0x21af9147/0", "type": 21, "name": "", "md5": "bf03f0..."}], "removed": []},
  {"path": "VERSION.TXT", "kind": "file", "size": 4, "base_md5": "2728ab...", "result_md5": "06c9c7..."}
 ]
}
```

A resource key is `0x<id>/<stream>` (multi-stream bundles hold some ids once per stream); its file is
`bundles/<path>/<16 hex digits of the id>_<stream>.bres`, a BNDL Explorer resource file (Export as .bres). A
bundle entry also has `"order"` when the mod moves resources around. Paths are relative to the game folder, with
`/`; a path that leaves the game folder makes the package refused. Only `format`, `name`, `platform` and each
file's `path` and `kind` (and a bundle's `resources`) are needed, so a package can also be zipped by hand.

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
python tests\test_port.py                 :: porting into a game bundle, the Genesys layout fix (runs without game files)
python tests\test_port_ui.py              :: the Port window driven with a fake imgui (runs without game files or a display)
python tests\test_gui.py                  :: the real window: drops, copy between bundles, clipboard, undo, save
python tests\test_audio_gui.py            :: sound preview: jump to a position, stop when another item is selected (silent)
python tests\test_fbx_gui.py              :: FBX export with textures, import back, an edited object, undo
python tests\test_anim_gui.py             :: damage animations on the car, a camera path, a skeleton
python tests\test_triggers_gui.py         :: the trigger map: move / add / delete, the grid rebuilt, undo
python tests\test_modpack.py              :: mod packages on temporary game folders: load order, conflicts, restore
python tests\test_launcher_gui.py         :: the Mod Launcher: make a package, the conflict question, apply, start
python tests\roundtrip_all.py "%BNDLX_PC%" "%BNDLX_PS3%"   :: every bundle saves byte-identical
```

`python -m bndlx FILES --screenshot out.png [--select ID]` renders the window, saves a screenshot and exits.

## Credits

Reverse engineering and tools: FrankHUN88453, with Claude. Dear ImGui by Omar Cornut; imgui-bundle by Pascal
Thomet. Icons: [Font Awesome Free](https://fontawesome.com) (CC BY 4.0), also used for the program icon.
