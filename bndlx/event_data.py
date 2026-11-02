"""Top-level event catalog reader used by the TypeScript event visualizer."""
from dataclasses import dataclass
import struct

from .ts_names import EVENT_NAMES


ROOT_TYPE_VERSION = 0xBC855D7F
EVENT_TYPES = {
    0x903BCC47: 'Offline event',
    0x1773054A: 'Online event',
    0x7E701662: 'Online challenge',
    0xBAF7B2C9: 'Vote event',
}


class EventDataError(ValueError):
    pass


@dataclass(frozen=True)
class EventArray:
    name: str
    offset: int
    count: int
    kind: str
    fmt: str
    editable: bool = True


@dataclass(frozen=True)
class EventRecord:
    index: int
    offset: int | None
    type_version: int | None
    name_id: int | None
    fields: tuple = ()
    arrays: tuple[EventArray, ...] = ()

    @property
    def kind(self):
        return EVENT_TYPES.get(self.type_version, 'Unknown event type')


@dataclass(frozen=True)
class EventCatalog:
    handle_entry: int
    handle_offset: int
    type_version: int
    unknown_id: int
    event_start_offset: int
    table_offset: int
    records: tuple[EventRecord, ...]
    fields: tuple


def event_name(record):
    """Resolve the event id stored by the binary to its known name when available."""
    if record.name_id is None:
        return None
    return EVENT_NAMES.get(record.name_id) or EVENT_NAMES.get(record.name_id - 1)


_ROOT_FIELDS = (
    ('header.mpType.mpEntry', 0x00, 'relative pointer', None, False),
    ('header.mpType.muOffset', 0x04, 'u32', '<I', False),
    ('header.muTypeVersion', 0x08, 'type version', '>I', False),
    ('unknownId', 0x0C, 'u32', '<I', True),
    ('eventStartOffset', 0x10, 'pointer', '<I', False),
    ('eventCount', 0x14, 'array count', '<I', False),
)

_OFFLINE_FIELDS = (
    ('autotestCheckpoints', 0x0C, 'pointer', '<I', False),
    ('cinematicName', 0x10, 'pointer', '<I', False),
    ('description', 0x14, 'pointer', '<I', False),
    ('gameChangerId', 0x18, 'u32 (big-endian)', '>I', True),
    ('gameMode', 0x1C, 'u32 (big-endian)', '>I', True),
    ('gamePack', 0x20, 'u32', '<I', True),
    ('uniqueId', 0x24, 'u32', '<I', True),
    ('cycleTimeOfDay', 0x28, 'float', '<f', True),
    ('finishTimeOfDay', 0x2C, 'float', '<f', True),
    ('sunDirection', 0x30, 'float', '<f', True),
    ('timeOfDay', 0x34, 'float', '<f', True),
    ('unknownFloat', 0x38, 'float', '<f', True),
    ('chevronList', 0x3C, 'pointer', '<I', False),
    ('roadSurfaceConditions', 0x40, 'u16', '<H', True),
    ('arrayCountForAutotestCheckpoints', 0x42, 'u16 array count', '<H', True),
    ('isAlternativeWeather', 0x44, 'bool', '<B', True),
    ('isRainActive', 0x45, 'bool', '<B', True),
    ('isThunderActive', 0x46, 'bool', '<B', True),
    ('overrideSunDirection', 0x47, 'bool', '<B', True),
    ('unknownBool', 0x48, 'bool', '<B', True),
    ('arrayCountForChevronList', 0x49, 'u8 array count', '<B', True),
    ('unknownByte', 0x4A, 'u8', '<B', True),
    ('trafficOverrides', 0x4C, 'bytes[8]', None, False),
    ('additionalAssets', 0x54, 'pointer', '<I', False),
    ('aiHints', 0x58, 'pointer', '<I', False),
    ('allowedVehicleList', 0x5C, 'resource reference', '<I', False),
    ('uniqueId2', 0x60, 'u32', '<I', True),
    ('checkpoints', 0x64, 'pointer', '<I', False),
    ('uniqueId3', 0x68, 'u32', '<I', True),
    ('defaultHeatLevels', 0x6C, 'pointer', '<I', False),
    ('eventIntro', 0x70, 'resource reference', '<I', False),
    ('eventOutro', 0x74, 'resource reference', '<I', False),
    ('feedbackMessageGroup', 0x78, 'resource reference', '<I', False),
    ('gameplayTriggers', 0x7C, 'pointer', '<I', False),
    ('uniqueId4', 0x80, 'u32', '<I', True),
    ('name', 0x84, 'event id', '<I', True),
    ('nameFormatted', 0x88, 'resource reference', '<I', False),
    ('nextStoryEvent', 0x8C, 'resource reference', '<I', False),
    ('uniqueId5', 0x90, 'u32', '<I', True),
    ('raceBalancingData', 0x94, 'resource reference', '<I', False),
    ('raceBalancingProfile', 0x98, 'resource reference', '<I', False),
    ('spawnPosition', 0x9C, 'resource reference', '<I', False),
    ('timeline', 0xA0, 'pointer', '<I', False),
    ('typeName', 0xA4, 'resource reference', '<I', False),
    ('targetSpeed', 0xA8, 'pointer', '<I', False),
    ('targetTime', 0xAC, 'pointer', '<I', False),
    ('trafficDensity', 0xB0, 'float', '<f', True),
    ('chevrons', 0xB4, 'pointer', '<I', False),
    ('customChevrons', 0xB8, 'pointer', '<I', False),
    ('gameplayConditions', 0xBC, 'pointer', '<I', False),
    ('aiPlayerInformation', 0xC0, 'pointer', '<I', False),
    ('alternateRoutes', 0xC4, 'pointer', '<I', False),
    ('checkpointInfo', 0xC8, 'pointer', '<I', False),
    ('targetScore', 0xCC, 'u32', '<I', True),
    ('demoMode', 0xD0, 'u16', '<H', True),
    ('arrayCountForAdditionalAssets', 0xD2, 'u16 array count', '<H', True),
    ('arrayCountForAiHints', 0xD4, 'u16 array count', '<H', True),
    ('arrayCountForAiPlayerInformation', 0xD6, 'u16 array count', '<H', True),
    ('arrayCountForAlternateRoutes', 0xD8, 'u16 array count', '<H', True),
    ('arrayCountForCheckpointInfo', 0xDA, 'u16 array count', '<H', True),
    ('arrayCountForCheckpoints', 0xDC, 'u16 array count', '<H', True),
    ('arrayCountForDefaultHeatLevels', 0xDE, 'u16 array count', '<H', True),
    ('arrayCountForChevrons', 0xE0, 'u16 array count', '<H', True),
    ('arrayCountForTargetSpeed', 0xE2, 'u16 array count', '<H', True),
    ('arrayCountForTargetTime', 0xE4, 'u16 array count', '<H', True),
    ('arrayCountForTimeline', 0xE6, 'u16 array count', '<H', True),
    ('copSpawning', 0xE8, 'bool', '<B', True),
    ('unknownBool2', 0xE9, 'bool', '<B', True),
    ('unknownBool3', 0xEA, 'bool', '<B', True),
    ('unknownBool4', 0xEB, 'bool', '<B', True),
    ('nitrousAllowed', 0xEC, 'bool', '<B', True),
    ('noRacingLineTraffic', 0xED, 'bool', '<B', True),
    ('unknownBool5', 0xEE, 'bool', '<B', True),
    ('startWithEngineOn', 0xEF, 'bool', '<B', True),
    ('trafficEnabled', 0xF0, 'bool', '<B', True),
    ('unknownBool6', 0xF1, 'bool', '<B', True),
    ('weaponsAllowed', 0xF2, 'bool', '<B', True),
    ('arrayCountForCustomChevrons', 0xF3, 'u8 array count', '<B', True),
    ('arrayCountForGameplayConditions', 0xF4, 'u8 array count', '<B', True),
    ('arrayCountForGameplayTriggers', 0xF5, 'u8 array count', '<B', True),
    ('laps', 0xF6, 'u8', '<B', True),
)

_BASE_FIELDS = tuple(field for field in _OFFLINE_FIELDS if field[1] <= 0x4A)
_ONLINE_FIELDS = (
    ('floatArrayHeader', 0x4C, 'float', '<f', True),
    ('floatArrayEntry', 0x50, 'float', '<f', True),
    ('arena', 0x54, 'resource reference', '<I', False),
    ('uniqueId2', 0x58, 'u32', '<I', True),
    ('uniqueId3', 0x5C, 'u32', '<I', True),
    ('uniqueIdArray', 0x60, 'pointer', '<I', False),
    ('route', 0x64, 'resource reference', '<I', False),
    ('arrayCountForFloatArray', 0x68, 'u16 array count', '<H', True),
    ('arrayCountForUniqueIdArray', 0x6A, 'u16 array count', '<H', True),
    ('requiresAirport', 0x6C, 'bool', '<B', True),
)
_CHALLENGE_FIELDS = (
    ('intro', 0x70, 'resource reference', '<I', False),
    ('uniqueId4', 0x74, 'u32', '<I', True),
    ('challengePartPointerTable', 0x78, 'pointer', '<I', False),
    ('eliminationType', 0x7C, 'u16', '<H', True),
    ('challengeType', 0x7E, 'u16', '<H', True),
    ('arrayCountForChallengeParts', 0x80, 'u8 array count', '<B', True),
)
_VOTE_FIELDS = (
    ('uniqueId4', 0x70, 'u32', '<I', True),
    ('uniqueId5', 0x74, 'u32', '<I', True),
    ('uniqueId6', 0x78, 'u32', '<I', True),
    ('uniqueId7', 0x7C, 'u32', '<I', True),
)


def _read_fields(data, base, fields):
    result = []
    for name, offset, kind, fmt, editable in fields:
        value = struct.unpack_from(fmt, data, base + offset)[0] if fmt else None
        result.append((name, base + offset, kind, value, fmt, editable))
    return tuple(result)


def _read_array(data, base, name, pointer_offset, count_offset, count_size, fmt, kind, inline=False):
    pointer = base + pointer_offset if inline else struct.unpack_from('<I', data, base + pointer_offset)[0]
    count_fmt = {1: '<B', 2: '<H', 4: '<I'}[count_size]
    count = struct.unpack_from(count_fmt, data, base + count_offset)[0]
    if not count:
        return None
    if not inline and pointer == 0:
        raise EventDataError(f'{name} has {count} entries but a null pointer')
    offset = pointer
    size = struct.calcsize(fmt)
    if offset < 0 or count > (len(data) - offset) // size:
        raise EventDataError(f'{name} array at {offset:#x} with {count} entries exceeds resource bounds')
    return EventArray(name, offset, count, kind, fmt)


_OFFLINE_ARRAYS = (
    ('autotestCheckpoints', 0x0C, 0x42, 2, '<I', 'u32'),
    ('additionalAssets', 0x54, 0xD2, 2, '<I', 'u32'),
    ('aiHints', 0x58, 0xD4, 2, '<I', 'u32'),
    ('checkpoints', 0x64, 0xDC, 2, '<I', 'u32'),
    ('defaultHeatLevels', 0x6C, 0xDE, 2, '<I', 'u32'),
    ('gameplayTriggers', 0x7C, 0xF5, 1, '<I', 'u32'),
    ('timeline', 0xA0, 0xE6, 2, '<I', 'u32'),
    ('targetSpeed', 0xA8, 0xE2, 2, '<f', 'float'),
    ('targetTime', 0xAC, 0xE4, 2, '<f', 'float'),
)
_ONLINE_ARRAYS = (
    ('floatArray', 0x4C, 0x68, 2, '<f', 'float', True),
    ('uniqueIdArray', 0x60, 0x6A, 2, '<I', 'u32', False),
)


def _read_arrays(data, base, specs):
    arrays = []
    for spec in specs:
        name, pointer_offset, count_offset, count_size, fmt, kind, *inline = spec
        array = _read_array(data, base, name, pointer_offset, count_offset, count_size, fmt, kind,
                            inline=bool(inline and inline[0]))
        if array is not None:
            arrays.append(array)
    return tuple(arrays)


def parse(data):
    """Read the root Genesys header and typed event pointer table, or return None for other objects."""
    data = memoryview(data)
    if len(data) < 12:
        return None
    root_type_version = struct.unpack_from('>I', data, 8)[0]
    if root_type_version != ROOT_TYPE_VERSION:
        return None
    if len(data) < 24:
        raise EventDataError('Event catalog header is truncated')
    handle_entry, handle_offset = struct.unpack_from('<iI', data, 0)
    unknown_id, event_start_offset, count = struct.unpack_from('<3I', data, 12)
    table_offset = event_start_offset
    table_end = table_offset + count * 4
    if event_start_offset < 24 or table_end > len(data):
        raise EventDataError(
            f'Invalid event pointer table: offset {event_start_offset:#x}, count {count}, size {len(data):#x}')
    root_fields = _read_fields(data, 0, _ROOT_FIELDS)
    root_fields = (
        (root_fields[0][0], root_fields[0][1], root_fields[0][2], handle_entry,
         root_fields[0][4], root_fields[0][5]),
        *root_fields[1:],
        ('allEvents', event_start_offset, f'Ptr[{count}]', event_start_offset, None, False),
    )
    records = []
    for index in range(count):
        pointer = struct.unpack_from('<i', data, table_offset + index * 4)[0]
        if pointer == 0:
            records.append(EventRecord(index, None, None, None))
            continue
        if pointer < 0 or pointer + 12 > len(data):
            raise EventDataError(f'Event {index} has an invalid object offset {pointer:#x}')
        type_version = struct.unpack_from('>I', data, pointer + 8)[0]
        name_id = None
        arrays = ()
        if type_version == 0x903BCC47:
            if pointer + 0x88 > len(data):
                raise EventDataError(f'Offline event {index} is truncated before its name id')
            name_id = struct.unpack_from('<I', data, pointer + 0x84)[0]
            if pointer + 0xF7 > len(data):
                raise EventDataError(f'Offline event {index} is truncated before its fixed fields')
            fields = _read_fields(data, pointer, _OFFLINE_FIELDS)
            arrays = _read_arrays(data, pointer, _OFFLINE_ARRAYS)
        elif type_version == 0x1773054A:
            if pointer + 0x6D > len(data):
                raise EventDataError(f'Online event {index} is truncated before its fixed fields')
            fields = _read_fields(data, pointer, _BASE_FIELDS + _ONLINE_FIELDS)
            arrays = _read_arrays(data, pointer, _ONLINE_ARRAYS)
        elif type_version == 0x7E701662:
            if pointer + 0x81 > len(data):
                raise EventDataError(f'Online challenge {index} is truncated before its fixed fields')
            fields = _read_fields(data, pointer, _BASE_FIELDS + _ONLINE_FIELDS + _CHALLENGE_FIELDS)
            arrays = _read_arrays(data, pointer, _ONLINE_ARRAYS)
        elif type_version == 0xBAF7B2C9:
            if pointer + 0x80 > len(data):
                raise EventDataError(f'Vote event {index} is truncated before its fixed fields')
            fields = _read_fields(data, pointer, _BASE_FIELDS + _ONLINE_FIELDS + _VOTE_FIELDS)
            arrays = _read_arrays(data, pointer, _ONLINE_ARRAYS)
        else:
            fields = ()
        records.append(EventRecord(index, pointer, type_version, name_id, fields, arrays))
    return EventCatalog(handle_entry, handle_offset, root_type_version, unknown_id, event_start_offset,
                        table_offset, tuple(records), root_fields)
