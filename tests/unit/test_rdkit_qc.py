from project_blends_compute.identity.chemistry import validate_structure


def test_rdkit_warnings_are_captured_as_structured_qc_not_printed(capsys):
    result = validate_structure(smiles="CC(C)=CCCC(C)C1C=CC(C)=CC1")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert result.parse_valid is True
    assert any(row["code"] == "omitted_undefined_stereo" for row in result.rdkit_messages or [])
    assert "rdkit_warning:omitted_undefined_stereo" in (result.notes or [])
