"""Random static handles: scientist surnames, unique among live agents."""
from __future__ import annotations

import random
import sqlite3

POOL: tuple[str, ...] = (
    "curie", "einstein", "bohr", "noether", "darwin", "franklin", "turing",
    "lovelace", "hopper", "newton", "maxwell", "faraday", "planck", "dirac",
    "feynman", "fermi", "heisenberg", "schrodinger", "pasteur", "mendel",
    "kepler", "galilei", "copernicus", "hubble", "sagan", "hawking", "penrose",
    "ramanujan", "euler", "gauss", "hilbert", "godel", "shannon", "neumann",
    "meitner", "rutherford", "dalton", "avogadro", "lavoisier", "linnaeus",
    "tesla", "volta", "ampere", "ohm", "hertz", "doppler", "boltzmann",
    "carson", "goodall", "mcclintock",
)


def allocate(conn: sqlite3.Connection) -> str:
    taken = {r["name"] for r in conn.execute(
        "SELECT name FROM agents WHERE status != 'offline'")}
    free = [n for n in POOL if n not in taken]
    if not free:
        raise RuntimeError("name pool exhausted")
    return random.choice(free)
