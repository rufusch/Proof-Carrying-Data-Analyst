"""The checked-in OpenAPI document is the runtime wire-schema authority."""
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator, FormatChecker

DOCUMENT = yaml.safe_load((Path(__file__).resolve().parents[1] / "docs/openapi.yaml").read_text(encoding="utf-8"))


def validate(name, value):
    schema = {"$ref": f"#/components/schemas/{name}", "components": DOCUMENT["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    return value

