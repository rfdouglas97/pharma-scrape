from pipeline_intel.extract.extractor import is_text_rich


def test_text_rich_when_phase_dense_page():
    text = " ".join(["Phase 1 Phase 2 Phase 3 Preclinical Filed Approved"] * 4)  # >=12 hits
    assert is_text_rich(text, is_document=False, linked_image_count=0) is True


def test_not_text_rich_when_sparse():
    assert is_text_rich("About us. Phase 1 program.", is_document=False, linked_image_count=0) is False


def test_not_text_rich_for_documents():
    text = " ".join(["Phase 1 Phase 2 Phase 3"] * 10)
    assert is_text_rich(text, is_document=True, linked_image_count=0) is False


def test_not_text_rich_when_linked_chart_present():
    text = " ".join(["Phase 1 Phase 2 Phase 3"] * 10)
    # a linked pipeline image means the chart is authoritative -> keep vision
    assert is_text_rich(text, is_document=False, linked_image_count=1) is False
