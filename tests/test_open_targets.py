"""Pure-function tests for Open Targets enrichment (no network)."""

from pipeline_intel.ontology.open_targets import (
    _DRUGTYPE_TO_MODALITY,
    _MAX_TARGETS_PER_MECHANISM,
    DrugAnnotation,
)


def test_drugtype_modality_map():
    assert _DRUGTYPE_TO_MODALITY["antibody"] == "mab"
    assert _DRUGTYPE_TO_MODALITY["antibody drug conjugate"] == "adc"
    assert _DRUGTYPE_TO_MODALITY["small molecule"] == "small_molecule"
    assert _DRUGTYPE_TO_MODALITY["unknown"] is None


def test_gene_family_threshold_is_sane():
    # Must allow bispecifics (2 targets) but exclude an ADC's tubulin payload (~15 genes).
    assert 2 <= _MAX_TARGETS_PER_MECHANISM < 10


def test_drug_annotation_dataclass():
    a = DrugAnnotation(chembl_id="CHEMBL1", drug_type="Antibody",
                       targets=[("PDCD1", "Programmed cell death protein 1")])
    assert a.targets[0][0] == "PDCD1"
