# tests/ptz/test_ptz_bootstrap.py
"""Offline tests for the async cloud-assisted auth bootstrap.

No real cloud calls: ``bootstrap`` is exercised with a mock client whose
``query_camera_p2p_info`` / ``request_camera_p2p_sign`` are coroutines returning
canned dicts. Async port of tools/ptz_poc/tests/test_auth.py.
"""
from custom_components.aqara_lanlink.ptz import bootstrap, ioctl

CAPTURED_P2P_ID = "AQARADE-359455-ZUXDL"
CAPTURED_DID_HEX = "415141524144450000057c1f5a5558444c000000"


class _MockClient:
    """Stand-in for AqaraCloudClient with async methods returning canned dicts."""

    async def query_camera_p2p_info(self, token, did):
        return {
            "initStringApp": "EFGGECDA:0:1:aqarager19kn",
            "p2pId": CAPTURED_P2P_ID,
            "p2pDevPublicKey": "cc" * 32,
        }

    async def request_camera_p2p_sign(self, token, did, app_pub_hex):
        return {"sign": "bb" * 32, "time": "1780000000", "p2pDevPublicKey": "cc" * 32}


async def test_bootstrap_assembles_creds():
    creds = await bootstrap.bootstrap(_MockClient(), "TOK", "USER", "lumi3.x")
    assert creds.cipher_key == "aqarager19kn"
    assert creds.p2p_id == "AQARADE-359455-ZUXDL"
    assert creds.did_blob == bytes.fromhex(CAPTURED_DID_HEX)
    assert len(bytes.fromhex(creds.app_pub_hex)) == 32
    assert creds.sign == "bb" * 32 and creds.time == "1780000000"
    assert creds.dev_pub_hex == "cc" * 32


class _NoDevPubKeyClient(_MockClient):
    """Sign response without p2pDevPublicKey -> tolerated (unused on LAN AUTH)."""

    async def request_camera_p2p_sign(self, token, did, app_pub_hex):
        return {"sign": "bb" * 32, "time": "1780000000"}


async def test_bootstrap_tolerates_missing_dev_pub_key():
    creds = await bootstrap.bootstrap(_NoDevPubKeyClient(), "TOK", "USER", "lumi3.x")
    assert creds.dev_pub_hex == ""
    assert creds.sign == "bb" * 32


def test_encode_did_byte_exact():
    assert (
        bootstrap.encode_did("AQARADE-359455-ZUXDL").hex()
        == "415141524144450000057c1f5a5558444c000000"
    )


def test_build_auth_ioctl_round_trips():
    creds = bootstrap.Creds(
        cipher_key="aqarager19kn",
        p2p_id=CAPTURED_P2P_ID,
        did_blob=bootstrap.encode_did(CAPTURED_P2P_ID),
        app_priv=None,
        app_pub_hex="aa" * 32,
        sign="bb" * 32,
        time="1700000000",
        dev_pub_hex="cc" * 32,
    )
    buf = bootstrap.build_auth_ioctl(creds, "lumi3.test", id=7)
    iotype, _id, body = ioctl.parse(buf)
    assert iotype == 4096
    assert _id == 7
    assert body == {
        "app_public_key": "aa" * 32,
        "app_sign": "bb" * 32,
        "device_id": "lumi3.test",
        "timestamp": "1700000000",
    }
