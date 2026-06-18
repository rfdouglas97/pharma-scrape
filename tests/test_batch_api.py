from pipeline_intel.extract.batch_api import extraction_output_config
from pipeline_intel.extract.schemas import ExtractionResult


def test_extraction_output_config_uses_json_schema():
    cfg = extraction_output_config()
    assert cfg["format"]["type"] == "json_schema"
    assert cfg["format"]["schema"]["title"] == ExtractionResult.model_json_schema()["title"]
    assert "assets" in cfg["format"]["schema"]["properties"]


def test_extraction_output_config_closes_object_schemas():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(extraction_output_config()["format"]["schema"])
