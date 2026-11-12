"""Every dependency HAYAT installs must be open source (app/licence_audit.py, docs/licence-audit.md)."""
from app.licence_audit import audit, classify, requirements


def test_every_installed_requirement_declares_an_osi_approved_licence():
    result = audit()
    assert result["checked"] >= 50
    assert result["not_osi"] == [], f"not open source (or licence unreadable): {result['not_osi']}"


def test_licences_are_read_from_what_each_package_declares():
    assert classify("Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSL-1.0 AND MIT")[1]
    assert classify("Apache-2.0 OR BSD-3-Clause") == ("Apache-2.0 / BSD-3-Clause", True)
    assert classify("Copyright (c) 2007 Someone\n\nRedistribution and use in source and binary forms, with or without")[1]
    assert classify("Proprietary") == ("Proprietary", False)
    assert classify("Free for non-commercial use") == ("Free for non-commercial use", False)  # free is not open source
    assert classify("") == ("", False)


def test_each_requirement_says_what_it_is_for_when_the_file_does():
    rows = {row["package"]: row for row in requirements()}
    assert "orbital" in rows["sgp4"]["purpose"] and rows["httpx"]["file"] == "requirements.txt"
