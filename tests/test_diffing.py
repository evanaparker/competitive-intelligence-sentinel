from diffing import compute_diff


def test_no_diff_for_identical_text():
    assert compute_diff("hello world", "hello world") == ""


def test_detects_insertion():
    assert compute_diff("hello world", "hello brave world") == "{+brave+}"


def test_detects_deletion():
    assert compute_diff("hello brave world", "hello world") == "[-brave-]"


def test_detects_replacement():
    assert compute_diff("price is 1.25", "price is 2.50") == "[-1.25-]{+2.50+}"


def test_detects_multiple_changes():
    old = "Starting at $500 per month with contract"
    new = "Starting at $750 per month, no contract required"
    result = compute_diff(old, new)
    assert "[-$500-]" in result
    assert "{+$750+}" in result
    assert "{+required+}" in result
