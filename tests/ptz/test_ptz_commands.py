from custom_components.aqara_lanlink.ptz import commands as c


def test_iotypes():
    assert (c.AUTH_IOTYPE, c.PTZ_IOTYPE, c.ZOOM_IOTYPE, c.POSITION_IOTYPE) == (4096, 4138, 4142, 4140)


def test_zoom_body_formats_one_decimal():
    assert c.zoom_body(2.0) == {"zoom": "2.0"}
    assert c.zoom_body(1.25) == {"zoom": "1.2"}


def test_position_body():
    assert c.position_body("abc") == {"position": "abc"}


def test_features_constants():
    assert c.PAN_TILT == "pan_tilt" and c.ZOOM == "zoom" and c.PRESETS == "presets"
