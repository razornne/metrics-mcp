"""The check that makes the rest of it mean anything."""

from __future__ import annotations

from metrics_mcp.verify import check

VALUES = {1240.0, 0.238, 49.0, 3.5}


def test_a_number_that_is_not_in_the_result_is_caught():
    out = check("Active accounts reached 1,300 in March.", VALUES)
    assert not out.ok
    assert out.unverified == ["1,300"]


def test_numbers_from_the_result_pass():
    out = check("Active accounts reached 1,240, up from 49.", VALUES)
    assert out.ok, out.unverified


def test_a_rate_may_be_written_as_a_percentage():
    # 0.238 in the result, "23.8%" in the prose: the same number.
    assert check("Retention was 23.8% that month.", VALUES).ok


def test_a_percentage_that_was_never_computed_is_still_caught():
    out = check("Retention was 31.0% that month.", VALUES)
    assert not out.ok
    assert out.unverified == ["31.0%"]


def test_rounding_to_fewer_places_is_allowed():
    # The text said 3.5 and the stored value is 3.5; saying "3" would be
    # rounding to zero places, which is also a reading of the same number.
    assert check("About 3 actions per user.", {3.4}).ok
    assert not check("About 4 actions per user.", {3.4}).ok


def test_thousands_separators_in_either_style():
    assert check("1,240 accounts", {1240.0}).ok
    assert check("1 240 accounts", {1240.0}).ok


def test_text_with_no_numbers_passes_trivially():
    out = check("Activation improved over the quarter.", VALUES)
    assert out.ok and out.found == []


def test_extra_numbers_can_be_declared_by_the_caller():
    # The caller knows the window is six months long; that is not a
    # measurement and the model is allowed to say it.
    assert check("Across 6 months, MRR held at 1,240.", VALUES, extra={6.0}).ok
    assert not check("Across 6 months, MRR held at 1,240.", VALUES).ok


def test_every_number_is_reported_not_just_the_first():
    out = check("It went from 900 to 1,100.", VALUES)
    assert out.unverified == ["900", "1,100"]


def test_reason_names_the_offending_numbers():
    assert "1,300" in check("reached 1,300", VALUES).reason()


def test_a_month_label_is_not_treated_as_a_number():
    # "2026-09" is the one thing every sentence about a time series contains,
    # and neither 2026 nor 9 is evidence of anything.
    out = check("Active accounts in 2026-09 was 1,240.", {1240.0})
    assert out.ok, out.unverified
    assert out.found == ["1,240"]


def test_a_long_number_is_not_matched_by_its_first_three_digits():
    out = check("We saw 2026 signups.", {1240.0})
    assert out.unverified == ["2026"]


def test_a_full_date_is_masked_too():
    assert check("On 2026-09-15 it was 49.", {49.0}).ok


def test_a_number_at_the_end_of_a_sentence_is_seen():
    # Regression. The trailing lookahead once excluded a following ".", so a
    # number before a full stop matched nothing at all and every such claim
    # passed unchecked — the guard was off for the most common sentence shape
    # there is.
    out = check("Active accounts in 2026-09 was 212.", {185.0})
    assert out.found == ["212"]
    assert not out.ok


def test_a_decimal_is_still_read_whole():
    assert check("It was 12.34 exactly.", {12.34}).found == ["12.34"]
    assert check("It was 12.34 exactly.", {12.0}).unverified == ["12.34"]
