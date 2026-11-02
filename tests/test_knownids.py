"""Known id tables (bndlx/knownids.py, generated from tools/hashes.txt). Needs no game files:  python tests/test_knownids.py"""
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bndlx import knownids as K, names as N, restypes  # noqa: E402

failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


check(all((~zlib.crc32(n.encode('latin1'))) & 0xFFFFFFFF == h for h, n in K.PARAMETERS.items()),
      'every material constant name hashes to its key (~crc32)')
check(K.GAMECHANGER[0x1DF30][0].startswith('Aston Martin V12 Vantage'), 'car id')
check(K.GAMECHANGER[0x8C6BB] == ('Rolling Stock', 'event'), 'event id')
check(K.GAMECHANGER[17127] == ('Base Game', 'pack'), 'pack id')
check(K.GAMECHANGER[390422][0].startswith('HeatLevel_0'), 'heat level id')
check(K.SHADERS[0x9EF86] == 'VehicleNFS13_Body_Textured', 'shader id')
check(N.gc_number((0x01000000 << 32) | 122627) == 122627 and K.GAMECHANGER[122627][0] == 'Aston Martin V12 Vantage',
      'a Genesys object id resolves through gc_number')
check(restypes.name(0x84) == 'WaveDictionary' and restypes.name(0x105) == 'VehicleList', 'resource types from the list')
check(restypes.name(0x80) == 'GinsuEngineSound', 'the curated type names are kept')
print('FAILED: ' + ', '.join(failures) if failures else 'all passed')
sys.exit(1 if failures else 0)
