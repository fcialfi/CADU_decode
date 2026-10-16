"""CCSDS Reed-Solomon (255,223) code (CCSDS 131.0-B), with the Berlekamp dual basis.

Field GF(2^8) with x^8 + x^7 + x^2 + x + 1, generator roots β^(112..143) with
β = α^11, 32 check symbols: up to 16 symbol errors corrected per codeword.
On the link the symbols are in the dual basis: they are turned into the
conventional representation before the decoding and back after it.

The decoder follows Phil Karn's (libfec) Berlekamp-Massey / Chien / Forney
implementation. ``codeblock_status`` checks a whole (interleaved) codeblock;
``syndromes_nonzero`` checks many codewords at once with numpy, when available.
"""

NN, NROOTS, FCR, PRIM = 255, 32, 112, 11
A0 = NN  # log of zero
IPRIM = 116  # 11 · 116 = 1 mod 255

_EXP = [0] * (2 * NN)
_LOG = [A0] * 256
_x = 1
for _i in range(NN):
    _EXP[_i] = _EXP[_i + NN] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x187


def _modnn(x: int) -> int:
    return x % NN


# Berlekamp dual basis: TAL converts conventional -> dual, TAL1 dual -> conventional (libfec).
_TAL_MATRIX = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)
TAL = [0] * 256
TAL1 = [0] * 256
for _i in range(256):
    _v = 0
    for _j in range(8):
        for _k in range(8):
            if _i & (1 << _k):
                _v ^= _TAL_MATRIX[7 - _k] & (1 << _j)
    TAL[_i] = _v
    TAL1[_v] = _i

# Generator polynomial, in log form.
_gen = [1] + [0] * NROOTS
_root = FCR * PRIM
for _i in range(NROOTS):
    _gen[_i + 1] = 1
    for _j in range(_i, 0, -1):
        _gen[_j] = (_gen[_j - 1] ^ _EXP[_modnn(_LOG[_gen[_j]] + _root)]) if _gen[_j] else _gen[_j - 1]
    _gen[0] = _EXP[_modnn(_LOG[_gen[0]] + _root)]
    _root += PRIM
GENPOLY = [_LOG[g] for g in _gen]


def encode(data: bytes) -> bytes:
    """The 32 check symbols of 223 data bytes, both in the dual basis (as on the link)."""
    if len(data) != NN - NROOTS:
        raise ValueError("223 data bytes expected")
    parity = [0] * NROOTS
    for byte in data:
        feedback = _LOG[TAL1[byte] ^ parity[0]]
        if feedback != A0:
            for j in range(1, NROOTS):
                parity[j] ^= _EXP[_modnn(feedback + GENPOLY[NROOTS - j])]
        parity = parity[1:] + [_EXP[_modnn(feedback + GENPOLY[0])] if feedback != A0 else 0]
    return bytes(TAL[p] for p in parity)


def encode_codeblock(frame: bytes, interleave: int) -> bytes:
    """Codeblock (frame + interleaved check symbols) of a transfer frame of 223 · ``interleave`` bytes."""
    if len(frame) != (NN - NROOTS) * interleave:
        raise ValueError(f"{(NN - NROOTS) * interleave} bytes expected")
    block = bytearray(NN * interleave)
    for i in range(interleave):
        word = frame[i::interleave]
        block[i::interleave] = word + encode(word)
    return bytes(block)


def decode(codeword: bytes) -> tuple[bytes, int]:
    """(corrected codeword, symbols corrected) of a 255-byte codeword in the dual basis.

    The number is -1 when the errors cannot be corrected (more than 16); the
    codeword is then returned as it was.
    """
    if len(codeword) != NN:
        raise ValueError("255 bytes expected")
    data = [TAL1[b] for b in codeword]
    s = [data[0]] * NROOTS
    for j in range(1, NN):
        dj = data[j]
        for i in range(NROOTS):
            s[i] = dj if s[i] == 0 else dj ^ _EXP[_modnn(_LOG[s[i]] + (FCR + i) * PRIM)]
    if not any(s):
        return bytes(codeword), 0
    s = [_LOG[x] for x in s]

    # Berlekamp-Massey: error locator polynomial lambda.
    lam = [1] + [0] * NROOTS
    b = [_LOG[x] for x in lam]
    el = 0
    for r in range(1, NROOTS + 1):
        discr = 0
        for i in range(r):
            if lam[i] and s[r - i - 1] != A0:
                discr ^= _EXP[_modnn(_LOG[lam[i]] + s[r - i - 1])]
        discr = _LOG[discr]
        if discr == A0:
            b = [A0] + b[:-1]
            continue
        t = [lam[0]] + [lam[i + 1] ^ _EXP[_modnn(discr + b[i])] if b[i] != A0 else lam[i + 1]
                        for i in range(NROOTS)]
        if 2 * el <= r - 1:
            el = r - el
            b = [A0 if lam[i] == 0 else _modnn(_LOG[lam[i]] - discr + NN) for i in range(NROOTS + 1)]
        else:
            b = [A0] + b[:-1]
        lam = t
    lam = [_LOG[x] for x in lam]
    deg_lambda = max(i for i in range(NROOTS + 1) if lam[i] != A0)

    # Chien search: roots of lambda, i.e. the error locations.
    reg = lam[:]
    roots, locs = [], []
    k = IPRIM - 1
    for i in range(1, NN + 1):
        q = 1
        for j in range(deg_lambda, 0, -1):
            if reg[j] != A0:
                reg[j] = _modnn(reg[j] + j)
                q ^= _EXP[reg[j]]
        if q == 0:
            roots.append(i)
            locs.append(k)
            if len(roots) == deg_lambda:
                break
        k = _modnn(k + IPRIM)
    if len(roots) != deg_lambda:
        return bytes(codeword), -1

    # Forney: error values.
    deg_omega = deg_lambda - 1
    omega = []
    for i in range(deg_omega + 1):
        tmp = 0
        for j in range(i, -1, -1):
            if s[i - j] != A0 and lam[j] != A0:
                tmp ^= _EXP[_modnn(s[i - j] + lam[j])]
        omega.append(_LOG[tmp])
    for root, loc in zip(roots, locs):
        num1 = 0
        for i in range(deg_omega, -1, -1):
            if omega[i] != A0:
                num1 ^= _EXP[_modnn(omega[i] + i * root)]
        num2 = _EXP[_modnn(root * (FCR - 1) + NN)]
        den = 0
        for i in range(min(deg_lambda, NROOTS - 1) & ~1, -1, -2):
            if lam[i + 1] != A0:
                den ^= _EXP[_modnn(lam[i + 1] + i * root)]
        if num1 and den:
            data[loc] ^= _EXP[_modnn(_LOG[num1] + _LOG[num2] + NN - _LOG[den])]
    return bytes(TAL[x] for x in data), len(roots)


def codeblock_status(codeblock: bytes, interleave: int) -> tuple[bytes, list[int]]:
    """(corrected codeblock, symbols corrected per codeword, -1 when uncorrectable).

    The codeblock holds ``interleave`` codewords interleaved symbol by symbol:
    codeword i is made of the bytes i, i + I, i + 2I, ...
    """
    block = bytearray(codeblock)
    counts = []
    for i in range(interleave):
        fixed, n = decode(bytes(block[i::interleave]))
        if n > 0:
            block[i::interleave] = fixed
        counts.append(n)
    return bytes(block), counts


def syndromes_nonzero(codeblocks, interleave: int):
    """Whether each codeblock (rows of a numpy uint8 array) has an error in any of its codewords.

    Fast check of many codeblocks at once; only those flagged need :func:`codeblock_status`.
    """
    import numpy as np

    blocks = np.asarray(codeblocks, dtype=np.uint8)
    n = blocks.shape[0]
    words = np.asarray(TAL1, dtype=np.uint8)[blocks].reshape(n, NN, interleave)
    words = words.transpose(0, 2, 1).reshape(n * interleave, NN)  # one row per codeword
    exp = np.asarray(_EXP, dtype=np.int32)
    log = np.asarray(_LOG, dtype=np.int32)
    mul = np.zeros((NROOTS, 256), dtype=np.uint8)  # mul[i, x] = x · β^(FCR + i)
    for i in range(NROOTS):
        step = (FCR + i) * PRIM
        mul[i, 1:] = exp[(log[1:] + step) % NN]
    rows = np.arange(NROOTS)[None, :]
    s = np.repeat(words[:, :1], NROOTS, axis=1)
    for j in range(1, NN):
        s = mul[rows, s] ^ words[:, j:j + 1]
    bad = s.any(axis=1).reshape(n, interleave)
    return bad.any(axis=1)
