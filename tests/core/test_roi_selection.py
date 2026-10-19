import pytest

from dmri_studio.core.roi_selection import apply_selection, parse_ids, select_names


def test_ids_preserve_original_order_and_deduplicate():
    assert parse_ids("41-45, 1, 3, 7-12, 25, 41", range(1, 101)) == [
        41,
        42,
        43,
        44,
        45,
        1,
        3,
        7,
        8,
        9,
        10,
        11,
        12,
        25,
    ]
    assert parse_ids("20, 0, 3", {0, 3, 20}) == [20, 0, 3]


@pytest.mark.parametrize("text", ["", "1,,3", "3-1", "-1", "1.0", "a", "1-1000000000", "7"])
def test_invalid_selection(text):
    with pytest.raises(ValueError):
        parse_ids(text, {0, 1, 3, 20})


def test_names_and_modes():
    assert select_names(
        "FRONTAL,insula", {3: "Left frontal", 20: "Right insula", 99: "Occipital"}
    ) == [3, 20]
    assert apply_selection([20, 3], [99, 20], "add") == [20, 3, 99]
    assert apply_selection([20, 3], [3], "remove") == [20]
    assert apply_selection([20], [99, 3], "replace") == [99, 3]
