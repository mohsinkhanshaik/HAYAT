"""Numerical, input-budget and evidence checks for the local CSV workbench."""
import hashlib
import json
import math

import pytest

from app.scientific_workbench import AnalysisCancelled, demo_profile, profile_csv, report_markdown


def csv_file(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "measurements.csv"
    path.write_text(text, encoding=encoding)
    return path


def test_numeric_summaries_missingness_and_known_correlations(tmp_path):
    profile = profile_csv(csv_file(tmp_path, "x,y,negative,batch\n1,2,4,A\n2,4,3,A\n3,6,2,B\n4,8,1,B\nNA,,,B"))
    x = profile["columns"][0]
    assert (profile["rows"], x["present"], x["missing"], x["unique"]) == (5, 4, 1, 4)
    assert (x["mean"], x["median"], x["q1"], x["q3"]) == (2.5, 2.5, 1.75, 3.25)
    assert x["stdev"] == pytest.approx(math.sqrt(5 / 3))
    assert sum(bin_["count"] for bin_ in x["histogram"]) == 4
    pairs = {(pair["x"], pair["y"]): pair for pair in profile["correlations"]}
    assert pairs[("x", "y")]["r"] == pytest.approx(1)
    assert pairs[("x", "negative")]["r"] == pytest.approx(-1)
    assert pairs[("x", "y")]["n"] == 4
    assert profile["columns"][3]["kind"] == "text"
    assert profile["columns"][3]["mean"] is None
    json.dumps(profile, allow_nan=False)


def test_mixed_numeric_values_report_invalid_without_imputation(tmp_path):
    text = "measurement\n1\n2\n3\n4\ninf\nnull\nN/A\nNaN\nnone\n"
    profile = profile_csv(csv_file(tmp_path, text))
    column = profile["columns"][0]
    assert (column["kind"], column["present"], column["numeric_count"], column["missing"], column["invalid"]) == ("numeric", 5, 4, 4, 1)
    assert column["mean"] == 2.5
    assert any("excluded" in warning for warning in profile["warnings"])
    assert profile["correlations"] == []


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_delimiter_bom_quoted_fields_and_ragged_rows(tmp_path, encoding):
    text = 'x;x;note\n1;2;"quoted;value"\n3;4\n5;6;okay;extra\n'
    profile = profile_csv(csv_file(tmp_path, text, encoding))
    assert profile["delimiter"] == ";"
    assert profile["headers"] == ["x", "x (2)", "note"]
    assert profile["preview"] == [["1", "2", "quoted;value"], ["3", "4", ""], ["5", "6", "okay"]]
    assert profile["columns"][2]["missing"] == 1
    assert any("2 rows" in warning for warning in profile["warnings"])


@pytest.mark.parametrize("newline", ["\r", "\r\n", "\n"])
def test_csv_newline_styles_preserve_records_and_quoted_multiline_fields(tmp_path, newline):
    path = tmp_path / "newlines.csv"
    path.write_bytes(newline.join(('x,note', '1,"first', 'second"', '2,okay')).encode("utf-8"))
    profile = profile_csv(path)
    assert profile["rows"] == 2
    assert profile["preview"][0][1] == "first" + newline + "second"


def test_budgets_distinguish_exact_row_limit_from_truncated_extract(tmp_path):
    path = csv_file(tmp_path, "x,y\n1,2\n2,4\n3,6\n")
    profile = profile_csv(path, max_rows=2)
    assert profile["rows"] == 2 and profile["row_limit_reached"]
    assert any("not a random sample" in warning for warning in profile["warnings"])
    exact = profile_csv(path, max_rows=3)
    assert exact["rows"] == 3 and not exact["row_limit_reached"]
    assert exact["columns"][0]["mean"] == 2


def test_constant_singleton_and_extreme_finite_values_are_json_safe(tmp_path):
    profile = profile_csv(csv_file(tmp_path, "extreme,constant\n1e308,4\n-1e308,4\n1e308,4\n-1e308,4\n"))
    assert profile["columns"][0]["mean"] == 0
    assert profile["columns"][0]["median"] == 0
    assert profile["columns"][0]["stdev"] == pytest.approx(1.1547005383792515e308)
    assert profile["correlations"] == []
    assert profile["columns"][1]["histogram"] == [{"low": 4.0, "high": 4.0, "count": 4}]
    json.dumps(profile, allow_nan=False)
    singleton = profile_csv(csv_file(tmp_path, "value\n5\n"))
    assert singleton["columns"][0]["stdev"] is None


def test_empty_header_only_and_invalid_encoding(tmp_path):
    for text in ("", "\n", ",,\n1,2,3\n"):
        with pytest.raises(ValueError):
            profile_csv(csv_file(tmp_path, text))
    empty = profile_csv(csv_file(tmp_path, "x,y\n"))
    assert empty["rows"] == 0 and empty["warnings"]
    invalid = tmp_path / "bad.csv"
    invalid.write_bytes(b"\xffinvalid")
    with pytest.raises(ValueError, match="UTF-8"):
        profile_csv(invalid)


def test_long_fields_too_many_columns_missing_paths_and_invalid_limits(tmp_path):
    with pytest.raises(ValueError, match="field"):
        profile_csv(csv_file(tmp_path, "x\n" + "a" * 16_385))
    with pytest.raises(ValueError, match="columns"):
        profile_csv(csv_file(tmp_path, ",".join(f"c{number}" for number in range(129))))
    with pytest.raises(ValueError, match="existing"):
        profile_csv(tmp_path / "absent.csv")
    for limit in (0, -1, True, 2.5):
        with pytest.raises(ValueError, match="positive integer"):
            profile_csv(tmp_path / "absent.csv", max_rows=limit)


def test_file_and_cell_budgets_are_enforced_and_malformed_quotes_rejected(tmp_path, monkeypatch):
    import app.scientific_workbench as workbench

    path = csv_file(tmp_path, "x,y\n1,2\n2,4\n3,6\n")
    monkeypatch.setattr(workbench, "MAX_CELLS", 4)
    profile = profile_csv(path)
    assert profile["rows"] == 2 and profile["row_limit_reached"]
    monkeypatch.setattr(workbench, "MAX_FILE_BYTES", 5)
    with pytest.raises(ValueError, match="file limit"):
        profile_csv(path)
    monkeypatch.undo()
    with pytest.raises(ValueError, match="could not be read"):
        profile_csv(csv_file(tmp_path, 'x,y\n"unterminated,3\n'))


def test_cancellation_is_observed_before_read_and_during_analysis(tmp_path):
    with pytest.raises(AnalysisCancelled):
        profile_csv(tmp_path / "not-read.csv", cancelled=lambda: True)
    polls = []

    def cancel():
        polls.append(True)
        return len(polls) > 5

    with pytest.raises(AnalysisCancelled):
        profile_csv(csv_file(tmp_path, "x,y\n" + "1,2\n" * 2000), cancelled=cancel)
    assert len(polls) == 6


def test_pairwise_complete_counts_and_correlation_column_budget(tmp_path):
    text = "x,y,z\n1,2,7\n2,4,7\n3,6,7\n4,,7\n"
    profile = profile_csv(csv_file(tmp_path, text))
    assert profile["correlations"] == [{"x": "x", "y": "y", "n": 3, "r": pytest.approx(1)}]
    wide = ",".join(f"x{number}" for number in range(18)) + "\n"
    wide += "\n".join(",".join(str(row * column) for column in range(1, 19)) for row in range(1, 5))
    limited = profile_csv(csv_file(tmp_path, wide))
    assert len(limited["correlations"]) == 120
    assert any("first 16" in warning for warning in limited["warnings"])


def test_demo_and_markdown_export_are_reproducible_and_escape_untrusted_headers(tmp_path):
    first, second = demo_profile(), demo_profile()
    first.pop("elapsed_seconds")
    second.pop("elapsed_seconds")
    assert first == second
    assert first["demo"] and first["name"] == first["path"] == "Built-in demo"
    assert first["rows"] == 48
    demo_report = report_markdown(demo_profile())
    assert "synthetic demonstration" in demo_report and "pairwise" in demo_report
    profile = profile_csv(csv_file(tmp_path, '"<script>|[link](url)",y\n1,2\n2,4\n3,6'))
    report = report_markdown(profile)
    assert "<script>" not in report
    assert "&lt;script&gt;\\|\\[link\\](url)" in report
    assert "no significance" in report and "sample standard deviation" in report


def test_source_digest_identifies_exact_bytes_including_bom_and_is_exported(tmp_path):
    path = tmp_path / "source.csv"
    original = b"\xef\xbb\xbfx;y\r\n1;2\r\n2;4\r\n3;6\r\n"
    path.write_bytes(original)
    profile = profile_csv(path, max_rows=2)
    expected = hashlib.sha256(original).hexdigest()
    assert profile["source_sha256"] == expected
    assert path.read_bytes() == original
    report = report_markdown(profile)
    assert f"SHA-256 of source bytes: {expected}" in report
    assert "Inspected delimiter: semicolon" in report
    assert "Effective inspected-row budget: 2" in report
    demo = demo_profile()
    assert not demo.get("source_sha256")
    assert "SHA-256" not in report_markdown(demo)
