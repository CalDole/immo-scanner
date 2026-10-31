import base64

from filter_memory import (
    COOKIE_NAME,
    clamp_limit,
    clamp_span,
    cookie_script,
    covers_full_span,
    decode_filters,
    encode_filters,
    selection_in_options,
    slider_ceiling,
    stored_limit,
    stored_span,
)


def test_filters_roundtrip_through_the_cookie_value():
    state = {
        "search": "kreuzberg",
        "sources": ["immoscout24"],
        "price": [200000, 400000],
        "commute_minutes": None,
        "new_builds": True,
        "hide_foreclosures": False,
    }
    assert decode_filters(encode_filters(state)) == state


def test_decode_rejects_blank_and_non_object_values():
    assert decode_filters(None) == {}
    assert decode_filters("not-base64") == {}
    payload = base64.urlsafe_b64encode(b"[1, 2]").decode().rstrip("=")
    assert decode_filters(payload) == {}


def test_clamp_span_uses_the_full_range_when_nothing_was_saved():
    assert clamp_span(None, 0.0, 900000.0, 10000.0, whole=False) == (0.0, 900000.0)
    assert clamp_span("nope", 1800, 2026, 1, whole=True) == (1800, 2026)


def test_clamp_span_orders_and_snaps_into_the_current_data():
    assert clamp_span([400.0, 200.0], 100.0, 500.0, 10.0, whole=False) == (200.0, 400.0)
    assert clamp_span([50.0, 900.0], 100.0, 500.0, 10.0, whole=False) == (100.0, 500.0)
    assert clamp_span([123.0, 456.0], 100.0, 500.0, 10.0, whole=False) == (120.0, 460.0)
    assert clamp_span([1890, 2024], 1800, 2026, 1, whole=True) == (1890, 2024)


def test_a_full_span_or_max_is_not_stored_as_a_filter():
    assert stored_span((100.0, 500.0), 100.0, 500.0, 10.0) is None
    assert stored_span((100.0, 400.0), 100.0, 500.0, 10.0) == [100, 400]
    assert stored_span((966.0, 10766.0), 966.0, 10784.0, 100.0) is None
    assert stored_span((60.0, 504.0), 60.0, 504.22, 1.0) is None
    assert stored_span((160.0, 500.0), 100.0, 500.0, 10.0) == [160, 500]
    assert covers_full_span((100.0, 400.0), 100.0, 500.0, 10.0) is False
    assert stored_limit(50.0, 50.0) is None
    assert stored_limit(20.0, 50.0) == 20


def test_slider_ceiling_is_the_last_step_at_or_below_the_data_max():
    assert slider_ceiling(18_432.7, 100.0) == 18_400
    assert slider_ceiling(18_400.0, 100.0) == 18_400
    assert slider_ceiling(47.0, 5.0) == 45
    assert slider_ceiling(100.0, 100.0) == 100
    assert slider_ceiling(50.0, 100.0) == 50


def test_clamp_limit_keeps_an_unset_max_at_the_current_high():
    assert clamp_limit(None, 47.0, 5.0) == 47.0
    assert clamp_limit(True, 47.0, 5.0) == 47.0
    assert clamp_limit(23, 47.0, 5.0) == 25.0
    assert clamp_limit(80, 47.0, 5.0) == 47.0


def test_selection_drops_options_that_are_gone():
    assert selection_in_options(["Mitte", "Gone"], ["Mitte", "Pankow"]) == ["Mitte"]
    assert selection_in_options("Mitte", ["Mitte"]) == []


def test_cookie_script_sets_the_filter_cookie():
    script = cookie_script("abc")
    assert COOKIE_NAME in script
    assert "abc" in script
    assert "SameSite=Lax" in script
    assert script.startswith("<script>")
    assert script.endswith("</script>")
