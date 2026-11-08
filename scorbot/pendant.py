"""Pure decoder for reply byte 3 (the teach pendant) as GetTPInfo decodes it (LEDGER.md PL107).

Key naming is from the vendor DLL's strings and is not checked on a real pendant (unverified).
"""

from dataclasses import dataclass

MODES: dict[int, tuple[str, bool]] = {
    0x40: ("In Teach mode not in Home", False),
    0xC0: ("In Teach mode in Home", True),
    0x7E: ("Auto not in Home", False),
    0xFE: ("Auto in Home", True),
    0x47: ("Not Dead Man not in Home", False),
    0x4F: ("Not Dead Man not in Home", False),
    0xF8: ("Not Dead Man in Home", True),
    0xF9: ("Not Dead Man in Home", True),
}

KEY_NAMES: dict[int, str] = {
    9: "Execute",
    10: "Record",
    11: "0",
    12: "Minus",
    13: "Plus",
    17: "Speed",
    18: "Insert",
    19: "9",
    20: "6",
    21: "3",
    25: "Move",
    26: "Spline",
    27: "8",
    28: "5",
    29: "2",
    33: "Abort",
    34: "Single Step",
    35: "7",
    36: "4",
    37: "1",
    41: "Open",
    42: "Control",
    43: "RUN",
    44: "CLR",
    45: "JOINTS",
}


@dataclass(frozen=True)
class PendantInfo:
    code: int
    mode: str | None
    key: int | None
    key_name: str | None
    pressed: bool | None
    in_home: bool


def decode_pendant(byte3: int) -> PendantInfo:
    """Decode a single teach pendant byte (reply byte 3).

    Eight whole values are mode codes; all other values are key codes.
    Raises ValueError for values outside 0..255.
    """
    if not isinstance(byte3, int) or isinstance(byte3, bool) or not (0 <= byte3 <= 255):
        raise ValueError(f"pendant byte must be an integer in 0..255, got {byte3!r}")

    if byte3 in MODES:
        mode_text, in_home = MODES[byte3]
        return PendantInfo(
            code=byte3,
            mode=mode_text,
            key=None,
            key_name=None,
            pressed=None,
            in_home=in_home,
        )

    key_number = byte3 & 0x3F
    key_name = KEY_NAMES.get(key_number, "Undefined")
    pressed = not bool(byte3 & 0x40)
    in_home = bool(byte3 & 0x80)
    return PendantInfo(
        code=byte3,
        mode=None,
        key=key_number,
        key_name=key_name,
        pressed=pressed,
        in_home=in_home,
    )
