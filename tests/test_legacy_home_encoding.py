"""Home wire-byte changes: pure source statements and fake endpoints, no Home execution.

The old/new slot literals are deliberate review evidence, not re-recorded fixtures.
Extracting the production field statements covers every duplicated site without
running homing or relying on its switches, timing or physical starting pose.
"""

import ast
from importlib.util import find_spec
from pathlib import Path
import unittest

from scorbot import Scorbot


# (low, sign, exact old slot text, exact corrected slot hex)
SLOTS = (
    (0, '0000', '00000000', '01000000'),
    (1, '0000', '01000000', '02000000'),
    (65535, '0000', 'ffff0000', '00000100'),
    (0, 'ffff', '0000ffff', '0100ffff'),
    (1, 'ffff', '0100ffff', '0200ffff'),
    (65535, 'ffff', 'ffffffff', '00000000'),
    (65536, '0000', '00100000', '01000100'),
    (-1, 'ffff', '01-0ffff', '0000ffff'),
)
SITES = ('pitch search (348-351)', 'pitch offset (387)', 'pitch braking (408)',
         'roll search (493)', 'roll offset (532)', 'roll braking (551)')
HOME = Path(__file__).resolve().parents[1] / 'openScorbot' / 'setHome.py'


def wrist_blocks():
    """Compile only each loop's signal_out construction and getStruct insertion."""
    tree = ast.parse(HOME.read_text(encoding='utf-8'))
    blocks = []
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.While)):
            continue
        body = loop.body
        for start, node in enumerate(body):
            if not (isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == 'signal_out'):
                continue
            end = start + 1
            while end < len(body) and isinstance(body[end], ast.AugAssign) \
                    and isinstance(body[end].target, ast.Name) and body[end].target.id == 'signal_out':
                end += 1
            # The next two statements read the echo and insert both wrist slots.
            insertion = body[end + 1]
            assert isinstance(insertion, ast.AugAssign) and insertion.target.id == 'cadena'
            assert isinstance(insertion.value, ast.Call) and insertion.value.func.attr == 'getStruct'
            module = ast.Module(body=body[start:end] + [insertion], type_ignores=[])
            blocks.append((node.lineno, compile(module, str(HOME), 'exec')))
    return [code for _, code in sorted(blocks)]


@unittest.skipUnless(find_spec('usb'), 'PyUSB is needed to import the legacy pure functions')
class HomeWristEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.libdef = Scorbot()._legacy('libdef')
        cls.blocks = wrist_blocks()

    def test_every_site_writes_the_explicit_new_bytes(self):
        self.assertEqual(len(self.blocks), len(SITES))
        # Distinct bytes in the held slots expose overwrites outside the wrist.
        held = bytes(range(24)).hex()
        for site, code in zip(SITES, self.blocks):
            for low, sign, old, new in SLOTS:
                for motor in (0, 1):
                    with self.subTest(site=site, low=low, sign=sign, motor=motor,
                                      old=old, new=new):
                        pairs = [[1, 'ffff'], [1, 'ffff']]
                        expected = ['0200ffff', '0200ffff']
                        pairs[motor], expected[motor] = [low, sign], new
                        namespace = dict(libdef=self.libdef, dato_in_1=pairs[0], dato_in_2=pairs[1],
                                         msg=held, cadena='')
                        exec(code, namespace)
                        self.assertEqual(namespace['signal_out'], ''.join(expected),
                                         f'{site}, motor {motor + 1}: old={old}, new={new}')
                        self.assertEqual(bytes.fromhex(namespace['cadena']),
                                         bytes.fromhex(held[:24] + ''.join(expected) + held[40:]))

    def test_old_bytes_and_the_one_count_difference_are_explicit(self):
        for low, sign, old, new in SLOTS:
            with self.subTest(low=low, sign=sign):
                self.assertEqual(self.libdef.detrans(low) + sign, old)
                self.assertEqual(self.libdef.countField(low, sign), new)
                if low < 0:
                    with self.assertRaises(ValueError):
                        bytes.fromhex(old)
                elif low <= 65535:
                    self.assertEqual(int.from_bytes(bytes.fromhex(new), 'little', signed=True),
                                     int.from_bytes(bytes.fromhex(old), 'little', signed=True) + 1)
                else:
                    self.assertEqual(int.from_bytes(bytes.fromhex(old), 'little', signed=True), 4096)

    def test_steps_can_leave_the_low_word_range(self):
        self.assertEqual(self.libdef.suma([65535, '0000'], 0, 0, 100, step=1), [65536, '0000'])
        self.assertEqual(self.libdef.resta([0, 'ffff'], 0, 0, 100, step=1), [-1, 'ffff'])

