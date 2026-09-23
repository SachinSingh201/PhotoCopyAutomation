import pytest
from backend.models.file import OrderFile
from backend.services.llm.parser import mock_llm_interpret


def test_llm_interpret_black_and_white():
    dummy_files = [
        OrderFile(upload_sequence=1, original_filename="doc.pdf", page_count=5),
    ]
    res = mock_llm_interpret("Print all in black and white", dummy_files)
    assert not res.ambiguity
    assert len(res.file_rules) == 1
    assert res.file_rules[0].color_mode == "bw"


def test_llm_interpret_ambiguous_instruction():
    # 2 files, instruction says "first 4 pages single sided" without specifying file
    dummy_files = [
        OrderFile(upload_sequence=1, original_filename="resume.pdf", page_count=6),
        OrderFile(upload_sequence=2, original_filename="assignment.pdf", page_count=20),
    ]
    res = mock_llm_interpret("First 4 pages single sided", dummy_files)
    assert res.ambiguity is True
    assert res.clarification_question is not None
