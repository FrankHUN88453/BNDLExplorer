"""Lua 5.1 bytecode (the UI scripts in TextFile resources): read, list (like luac -l), change constants, write.

The game's chunks are standard Lua 5.1 on both platforms (header 1B 'Lua' 51 00 01 04 04 04 08 00: little-endian,
4-byte int / size_t / instruction, double numbers), so PS3 scripts work on PC as they are. NFS13.exe also has the
Lua parser, so a script given as source text should load as well (lua_load tells the two apart by the first byte).

  chunk = luabc.read(data); listing = luabc.listing(chunk); chunk.main.constants[i] = 'new'; luabc.write(chunk)
"""
import struct

SIGNATURE = b'\x1bLua'

OPS = ('MOVE', 'LOADK', 'LOADBOOL', 'LOADNIL', 'GETUPVAL', 'GETGLOBAL', 'GETTABLE', 'SETGLOBAL', 'SETUPVAL',
       'SETTABLE', 'NEWTABLE', 'SELF', 'ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'POW', 'UNM', 'NOT', 'LEN', 'CONCAT',
       'JMP', 'EQ', 'LT', 'LE', 'TEST', 'TESTSET', 'CALL', 'TAILCALL', 'RETURN', 'FORLOOP', 'FORPREP', 'TFORLOOP',
       'SETLIST', 'CLOSE', 'CLOSURE', 'VARARG')
ABX = {'LOADK', 'GETGLOBAL', 'SETGLOBAL', 'CLOSURE'}
ASBX = {'JMP', 'FORLOOP', 'FORPREP'}


class LuaError(ValueError):
    pass


class Nil:
    """The nil constant (None is not used so that a list of constants can be edited without ambiguity)."""
    def __repr__(self):
        return 'nil'

    def __eq__(self, other):
        return isinstance(other, Nil)

    def __hash__(self):
        return 0


NIL = Nil()


class Function:
    __slots__ = ('source', 'line', 'lastline', 'nups', 'nparams', 'vararg', 'maxstack', 'code', 'constants',
                 'protos', 'lineinfo', 'locals', 'upvalues')


class Chunk:
    __slots__ = ('header', 'e', 'int_size', 'size_t', 'num_size', 'integral', 'main')


def is_bytecode(data):
    return bytes(data[:4]) == SIGNATURE


class _Reader:
    def __init__(self, data):
        self.d = bytes(data)
        self.o = 0

    def take(self, n):
        if self.o + n > len(self.d):
            raise LuaError(f'truncated at {self.o:#x}')
        v = self.d[self.o:self.o + n]
        self.o += n
        return v

    def u8(self):
        return self.take(1)[0]


def read(data):
    r = _Reader(data)
    h = r.take(12)
    if h[:4] != SIGNATURE:
        raise LuaError('not Lua bytecode')
    if h[4] != 0x51 or h[5] != 0:
        raise LuaError(f'Lua version {h[4]:#x} / format {h[5]} (only 5.1 is read)')
    c = Chunk()
    c.header = h
    c.e = '<' if h[6] == 1 else '>'
    c.int_size, c.size_t, ins, c.num_size, c.integral = h[7], h[8], h[9], h[10], h[11]
    if ins != 4 or c.int_size not in (4, 8) or c.size_t not in (4, 8) or c.num_size not in (4, 8):
        raise LuaError(f'unsupported sizes {h[7:12].hex()}')
    ifmt = c.e + ('i' if c.int_size == 4 else 'q')
    sfmt = c.e + ('I' if c.size_t == 4 else 'Q')
    nfmt = c.e + ({4: 'i', 8: 'q'} if c.integral else {4: 'f', 8: 'd'})[c.num_size]

    def int_():
        return struct.unpack(ifmt, r.take(c.int_size))[0]

    def string():
        n = struct.unpack(sfmt, r.take(c.size_t))[0]
        if n == 0:
            return None
        return r.take(n)[:-1].decode('latin1')

    def function():
        f = Function()
        f.source = string()
        f.line, f.lastline = int_(), int_()
        f.nups, f.nparams, f.vararg, f.maxstack = r.u8(), r.u8(), r.u8(), r.u8()
        n = int_()
        f.code = list(struct.unpack(c.e + f'{n}I', r.take(4 * n)))
        f.constants = []
        for _ in range(int_()):
            t = r.u8()
            if t == 0:
                f.constants.append(NIL)
            elif t == 1:
                f.constants.append(bool(r.u8()))
            elif t == 3:
                f.constants.append(struct.unpack(nfmt, r.take(c.num_size))[0])
            elif t == 4:
                f.constants.append(string() or '')
            else:
                raise LuaError(f'constant type {t} at {r.o - 1:#x}')
        f.protos = [function() for _ in range(int_())]
        n = int_()
        f.lineinfo = list(struct.unpack(ifmt[0] + f'{n}' + ifmt[1], r.take(c.int_size * n)))
        f.locals = [(string(), int_(), int_()) for _ in range(int_())]
        f.upvalues = [string() for _ in range(int_())]
        for i in f.code:
            if i & 0x3F >= len(OPS):
                raise LuaError(f'unknown opcode {i & 0x3F}')
        return f

    c.main = function()
    if r.o != len(r.d):
        raise LuaError(f'{len(r.d) - r.o} bytes after the chunk')
    return c


def write(c):
    ifmt = c.e + ('i' if c.int_size == 4 else 'q')
    sfmt = c.e + ('I' if c.size_t == 4 else 'Q')
    nfmt = c.e + ({4: 'i', 8: 'q'} if c.integral else {4: 'f', 8: 'd'})[c.num_size]
    out = bytearray(c.header)

    def int_(v):
        out.extend(struct.pack(ifmt, v))

    def string(s):
        if s is None:
            out.extend(struct.pack(sfmt, 0))
            return
        raw = s.encode('latin1') + b'\0'
        out.extend(struct.pack(sfmt, len(raw)))
        out.extend(raw)

    def function(f):
        string(f.source)
        int_(f.line)
        int_(f.lastline)
        out.extend(bytes((f.nups, f.nparams, f.vararg, f.maxstack)))
        int_(len(f.code))
        out.extend(struct.pack(c.e + f'{len(f.code)}I', *f.code))
        int_(len(f.constants))
        for k in f.constants:
            if isinstance(k, Nil):
                out.append(0)
            elif isinstance(k, bool):
                out.extend((1, int(k)))
            elif isinstance(k, (int, float)):
                out.append(3)
                out.extend(struct.pack(nfmt, int(k) if c.integral else float(k)))
            elif isinstance(k, str):
                out.append(4)
                string(k)
            else:
                raise LuaError(f'constant {k!r} cannot be written')
        int_(len(f.protos))
        for p in f.protos:
            function(p)
        int_(len(f.lineinfo))
        for v in f.lineinfo:
            int_(v)
        int_(len(f.locals))
        for name, a, b in f.locals:
            string(name)
            int_(a)
            int_(b)
        int_(len(f.upvalues))
        for u in f.upvalues:
            string(u)

    function(c.main)
    return bytes(out)


def functions(c):
    """(path, Function) for every function, depth first: path '0' = main, '0.2' = its third closure."""
    out = []

    def walk(f, path):
        out.append((path, f))
        for i, p in enumerate(f.protos):
            walk(p, f'{path}.{i}')

    walk(c.main, '0')
    return out


def constant_text(k):
    if isinstance(k, str):
        return '"' + k.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
    if isinstance(k, bool):
        return 'true' if k else 'false'
    if isinstance(k, float) and k.is_integer() and abs(k) < 1e15:
        return str(int(k))
    return repr(k)


def decode(i):
    op = OPS[i & 0x3F]
    a = (i >> 6) & 0xFF
    c = (i >> 14) & 0x1FF
    b = (i >> 23) & 0x1FF
    bx = (i >> 14) & 0x3FFFF
    return op, a, b, c, bx, bx - 131071


def listing(c, name='chunk'):
    """A luac -l -l style listing: every function with its instructions, constants, locals and upvalues."""
    lines = []
    for path, f in functions(c):
        kind = 'main' if path == '0' else 'function'
        lines.append(f'{kind} <{f.source or name}:{f.line},{f.lastline}> [{path}] ({len(f.code)} instructions)')
        lines.append(f'{f.nparams}{"+" if f.vararg else ""} params, {f.maxstack} slots, {f.nups} upvalues, '
                     f'{len(f.locals)} locals, {len(f.constants)} constants, {len(f.protos)} functions')

        def rk(x):
            return constant_text(f.constants[x & 0xFF]) if x & 0x100 and (x & 0xFF) < len(f.constants) else None

        for pc, ins in enumerate(f.code):
            op, a, b, cc, bx, sbx = decode(ins)
            line = f.lineinfo[pc] if pc < len(f.lineinfo) else 0
            if op in ABX:
                args = f'{a} {-1 - bx if op != "CLOSURE" else bx}'
            elif op in ASBX:
                args = f'{a} {sbx}'
            else:
                args = f'{a} {b if b < 0x100 else -1 - (b & 0xFF)} {cc if cc < 0x100 else -1 - (cc & 0xFF)}'
            note = ''
            if op in ('LOADK', 'GETGLOBAL', 'SETGLOBAL') and bx < len(f.constants):
                note = constant_text(f.constants[bx])
            elif op in ('GETUPVAL', 'SETUPVAL') and b < len(f.upvalues):
                note = f.upvalues[b] or ''
            elif op in ('GETTABLE', 'SELF'):
                note = rk(cc) or ''
            elif op in ('SETTABLE', 'ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'POW', 'EQ', 'LT', 'LE'):
                note = ' '.join(x for x in (rk(b) or '-', rk(cc) or '-') if x) if (rk(b) or rk(cc)) else ''
            elif op in ASBX:
                note = f'to {pc + 2 + sbx}'
            elif op == 'CLOSURE':
                note = f'[{path}.{bx}]'
            lines.append(f'\t{pc + 1}\t[{line}]\t{op:<9}\t{args}' + (f'\t; {note}' if note else ''))
        if f.constants:
            lines.append(f'constants ({len(f.constants)}):')
            for i, k in enumerate(f.constants):
                lines.append(f'\t{i + 1}\t{constant_text(k)}')
        if f.locals:
            lines.append(f'locals ({len(f.locals)}):')
            for i, (nm, s, e) in enumerate(f.locals):
                lines.append(f'\t{i}\t{nm}\t{s + 1}\t{e + 1}')
        if f.upvalues:
            lines.append(f'upvalues ({len(f.upvalues)}):')
            for i, u in enumerate(f.upvalues):
                lines.append(f'\t{i}\t{u}')
        lines.append('')
    return '\n'.join(lines)
