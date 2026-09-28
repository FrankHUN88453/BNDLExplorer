"""Resource type names (from the PS3 prototype's debug data) and short descriptions."""

NAMES = {
    0x001: 'Texture', 0x002: 'Material', 0x003: 'VertexDescriptor', 0x004: 'VertexProgramState',
    0x005: 'Renderable', 0x006: 'MaterialState', 0x007: 'SamplerState', 0x008: 'ShaderProgramBuffer',
    0x014: 'GenesysType', 0x015: 'GenesysObject', 0x030: 'Font', 0x050: 'InstanceList', 0x051: 'Model',
    0x052: 'ColourCube', 0x053: 'Shader', 0x060: 'PolygonSoupList', 0x068: 'Type 0x68', 0x070: 'TextFile',
    0x074: 'LuaData', 0x080: 'Type 0x80', 0x081: 'Wave', 0x082: 'WaveContainerTable', 0x090: 'ZoneList',
    0x091: 'WorldPaintMap', 0x0B0: 'AnimationList', 0x0B1: 'PathAnimation', 0x0B2: 'Skeleton',
    0x0B3: 'Animation', 0x0C0: 'CgsVertexProgramState', 0x0C1: 'CgsProgramBuffer',
    0x105: 'VehicleList', 0x106: 'VehicleGraphicsSpec', 0x200: 'AIData', 0x201: 'LocalisedText',
    0x202: 'TriggerData', 0x203: 'RoadData', 0x204: 'DynamicInstanceList', 0x205: 'WorldObject',
    0x206: 'ZoneHeader', 0x207: 'VehicleSound', 0x208: 'RoadData2', 0x209: 'CharacterSpec',
    0x20C: 'ReverbRoadData', 0x20D: 'CameraTake', 0x20E: 'CameraTakeList', 0x20F: 'GroundcoverCollection',
    0x210: 'ControlMesh', 0x211: 'CutsceneData', 0x212: 'CutsceneList', 0x213: 'LightInstanceList',
    0x214: 'GroundcoverInstances', 0x215: 'CompoundObject', 0x216: 'CompoundInstanceList',
    0x217: 'PropObject', 0x218: 'PropInstanceList', 0x301: 'BearEffect', 0x302: 'BearGlobalParameters',
    0x303: 'ConvexHull', 0x501: 'HSMData', 0x701: 'TrafficLaneData',
}

T_TEXTURE, T_GTYPE, T_GOBJECT, T_CUBE, T_TEXT, T_STRINGS = 0x001, 0x014, 0x015, 0x052, 0x070, 0x201

# file extension used when a resource is exported as its own format
EXPORT_EXT = {T_TEXTURE: '.dds', T_TEXT: '.txt', T_STRINGS: '.csv', T_CUBE: '.png'}


def name(type_id):
    return NAMES.get(type_id, f'Type {type_id:#x}')
