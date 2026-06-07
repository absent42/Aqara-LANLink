import json
import pathlib

import pytest

from custom_components.aqara_lanlink.ptz import cs2

PAIRS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures/cipher_pairs_sample.json").read_text()
)


@pytest.mark.parametrize("p", PAIRS)
def test_matches_captured_pair(p):
    src = bytes.fromhex(p["in"])
    dst = bytes.fromhex(p["out"])
    if p["op"] == "encrypt":
        assert cs2.encrypt(p["key"], src) == dst
        assert cs2.decrypt(p["key"], dst) == src
    else:
        assert cs2.decrypt(p["key"], src) == dst
        assert cs2.encrypt(p["key"], dst) == src
