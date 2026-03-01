from __future__ import annotations

from .bugcrowd import collect as collect_bugcrowd
from .hackerone import collect as collect_hackerone
from .independent import collect as collect_independent
from .intigriti import collect as collect_intigriti
from .openbugbounty import collect as collect_openbugbounty
from .yeswehack import collect as collect_yeswehack
from .base import RawProgram

COLLECTORS = [
    collect_hackerone,
    collect_bugcrowd,
    collect_intigriti,
    collect_yeswehack,
    collect_openbugbounty,
    collect_independent,
]


def get_all_programs() -> list[RawProgram]:
    combined: list[RawProgram] = []
    for collect in COLLECTORS:
        combined.extend(collect())
    return combined
