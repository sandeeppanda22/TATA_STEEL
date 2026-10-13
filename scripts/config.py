"""Canonical configuration for Tata Steel Road Works dashboard.
Everything here is DERIVED FROM USER RULINGS (2026-10-09), not inferred:

  - ACTIVITY_SEQUENCE : the single overall activity order (user ruling #3).
    Not every road has every step, but present steps follow this order.
    Used for %-based dependency gating (user ruling #4): an activity's
    actual % may not exceed the previous present activity's actual %.

  - MAJOR_WEIGHTS : the 5 "major" activities that carry road weightage
    (user ruling #2). If a major is absent on a road, its weight rolls
    forward to the immediate next PRESENT major (and to the previous one
    only if nothing follows). Non-major activities carry 0 weight.

  - NAME_MAP : normalises the many raw spellings in the source sheets to
    canonical names. Raw names NOT present here are reported as UNMAPPED
    and must get a ruling before they are placed in the sequence.
"""

# Canonical ordered activities (user ruling #3). Weight 0 unless a major.
ACTIVITY_SEQUENCE = [
    "Vegetation Clearance",
    "Six Party Clearance",
    "Heap Cutting for Road Diversion",
    "Diversion Road Making",
    "Excavation",            # major
    "Soil Disposal",
    "GSB",                   # major
    "WMM",                   # major
    "DLC",                   # major
    "Shuttering",
    "Reinforcement",
    "Salitex Board",
    "Road",                  # major (RCC / Paver laying)
    "Bitumen Paint",
    "Kerb Wall",
    "Angle Fixing",
    "RCC Drain",             # tracked separately for drain %
]

# user ruling #2 - base weights for the 5 majors (sum = 100)
MAJOR_WEIGHTS = {
    "Excavation": 25,
    "GSB": 10,
    "WMM": 15,
    "DLC": 20,
    "Road": 30,
}
MAJOR_ORDER = ["Excavation", "GSB", "WMM", "DLC", "Road"]

# Raw source spelling -> canonical name. Case-insensitive match on stripped text.
NAME_MAP = {
    "vegitation clearance": "Vegetation Clearance",
    "six party clearance": "Six Party Clearance",
    "(heap cutting for road diversion)": "Heap Cutting for Road Diversion",
    "diversion road making": "Diversion Road Making",
    "excavation": "Excavation",
    "excavation(box cutting)": "Excavation",
    "soil disposal": "Soil Disposal",
    "gsb": "GSB",
    "wmm": "WMM",
    "dlc": "DLC",
    "shuttering": "Shuttering",
    "reinforcement": "Reinforcement",
    "steel": "Reinforcement",
    "salitex board": "Salitex Board",
    "salitex board ": "Salitex Board",
    "shalitex board": "Salitex Board",
    # NOTE: bare "rcc" is deliberately NOT in this table - it is ambiguous
    # (see RCC_BARE_KEY handling in normalize()) and resolved per road_type.
    "rcc road": "Road",
    "rcc(reinforcemnt+shutering)": "Road",
    "paver laying": "Road",
    "bitumen paint": "Bitumen Paint",
    "kerb wall": "Kerb Wall",
    "kerbwall": "Kerb Wall",
    "angle fixing": "Angle Fixing",
    "angle": "Angle Fixing",
    "rcc drain": "RCC Drain",
}

# Raw names deliberately left UNMAPPED - these appear in the source but were
# NOT in the user-supplied sequence. They need a ruling (where in the order,
# and whether they carry any weight). Builder reports any it encounters.
KNOWN_UNMAPPED = {
    "dismantling", "dismentalling of paver",
    "subgrade preparation", "subgrade preparation ",
    "profile correction",
    "levelling &dressing", "levelling and dressing",
    "pcc",
}


def normalize(raw, road_type=None):
    """Return (canonical_name, is_mapped). Unmapped names pass through as-is.

    road_type-conditional rule (user ruling 2026-10-10): RCC and Paver are
    two mutually exclusive road finishing types - a road is never both. A
    bare "RCC" row therefore means two different things depending on which
    type the road is:
      - On a PAVER-type road, the ROAD major is "Paver Laying"; a bare
        "RCC" row is NOT a second ROAD major, it is an RCC Drain entry.
      - On an RCC-type road (or any other/unknown road_type), bare "RCC"
        IS the ROAD major.
    Full/unambiguous spellings ("RCC RoAD", "RCC(reinforcemnt+shutering)")
    are untouched by this rule and stay mapped to "Road" via NAME_MAP.
    """
    key = " ".join((raw or "").strip().split()).lower()
    if key == "rcc":
        if road_type and "paver" in str(road_type).strip().lower():
            return "RCC Drain", True
        return "Road", True
    if key in NAME_MAP:
        return NAME_MAP[key], True
    return (raw or "").strip(), False


def road_weightage(present_canonical):
    """Distribute MAJOR_WEIGHTS across the majors actually present on a road,
    rolling an absent major's weight forward to the next present major
    (user ruling #2). Returns {major_name: weight}."""
    present = [m for m in MAJOR_ORDER if m in present_canonical]
    if not present:
        return {}
    weights = {m: 0 for m in present}
    carry = 0
    for m in MAJOR_ORDER:
        base = MAJOR_WEIGHTS[m] + carry
        if m in weights:
            weights[m] = base
            carry = 0
        else:
            carry = base
    if carry:  # nothing present after the last absent major -> last present
        weights[present[-1]] += carry
    return weights
