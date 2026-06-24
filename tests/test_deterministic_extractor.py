from pathlib import Path

import pytest

from pipeline_intel.extract.deterministic import extract_structured_pipeline
from pipeline_intel.ingest.render import extract_pipeline_image_urls

# These fixtures are real scraped pages under artifacts/, which is gitignored — present
# locally, absent on a fresh CI runner. Skip rather than fail when the file isn't there.
MEIRAGTX_HTML = Path("artifacts/meiragtx/2026-06-17/01KVBJ1D_c89735f9cdfe/page.html")
KRYSTAL_HTML = Path("artifacts/krystal-biotech/2026-06-17/01KVC25H_28303cf97c7f/page.html")


@pytest.mark.skipif(not MEIRAGTX_HTML.exists(), reason="local scrape artifact not present (gitignored)")
def test_meiragtx_structured_pipeline_extracts_17_programs():
    html = MEIRAGTX_HTML.read_text()
    result = extract_structured_pipeline(html)
    assert result is not None
    rows = [(a.preferred_name, a.programs[0].indication_verbatim, a.programs[0].phase_verbatim)
            for a in result.assets]
    assert len(rows) == 17
    assert ("AAV-ABCA4", "Stargardt’s disease", "Preclinical") in rows
    assert ("AAV-AIPL1", "AIPL1-LCA4 congenital blindness", "Specials License") in rows
    assert ("AAV-BDNF2", "MC4R/BDNF genetic obesity", "Preclinical") in rows
    assert ("AAV-GAD2", "Parkinson's disease", "Phase 2") in rows
    assert ("AAV-hAQP1", "Radiation-induced xerostomia", "Phase 3") in rows
    assert ("Botaretigene sparoparvovec", "X-linked RP (RPGR)", "Phase 3") in rows


@pytest.mark.skipif(not KRYSTAL_HTML.exists(), reason="local scrape artifact not present (gitignored)")
def test_krystal_pipeline_image_is_detected_as_visual_evidence():
    html = KRYSTAL_HTML.read_text()
    assert extract_structured_pipeline(html) is None

    urls = extract_pipeline_image_urls("https://www.krystalbio.com/science/pipeline/", html)

    assert urls == ["https://www.krystalbio.com/wp-content/uploads/2023/10/pipeline-image-new-2.jpg"]
