"""Public Openness block-export dialect used by the synthetic PLC XML.

This is not a captured TIA Portal file. Element names follow the publicly
documented TIA Openness block export shape so SIN-93 can align to a later
owner-supplied conformance sample. Until that sample exists, no test may
claim TIA-export compatibility.
"""

from __future__ import annotations

DIALECT_NAME = "tia-openness-block-export"
ENGINEERING_VERSION = "V17"

REQUIRED_ELEMENTS = (
    "Document",
    "Engineering",
    "DocumentInfo",
    "SW.Blocks.OB",
    "SW.Blocks.FB",
    "SW.Blocks.FC",
    "SW.Blocks.GlobalDB",
    "AttributeList",
    "ObjectList",
    "SW.Blocks.CompileUnit",
    "NetworkSource",
)

# Typical Openness interface URI recorded for later alignment. Synthetic XML
# in this dataset is un-namespaced so it stays reviewable; SIN-93 must not
# treat missing xmlns as proof of a real export.
INTERFACE_URI = "http://www.siemens.com/automation/Openness/SW/Interface/v5"

CONFORMANCE_STATUS = "placeholder"
CONFORMANCE_EXPECTED_PATH = "conformance/sample.xml"

SYNTHETIC_XML_BANNER = (
    "synthetic generator output; NOT a TIA Portal export. dialect: tia-openness-block-export"
)


def dialect_document() -> dict:
    return {
        "name": DIALECT_NAME,
        "engineering_version": ENGINEERING_VERSION,
        "source": (
            "Public TIA Portal Openness block-export element names. Not a captured TIA file."
        ),
        "required_elements": list(REQUIRED_ELEMENTS),
        "interface_uri": INTERFACE_URI,
        "namespaces_in_synthetic_xml": False,
        "conformance_sample": {
            "status": CONFORMANCE_STATUS,
            "expected_path": CONFORMANCE_EXPECTED_PATH,
            "provenance_template": "conformance/provenance.template.json",
            "notes": (
                "Do not replace the placeholder with generator XML. "
                "SIN-93 waits for a legally cleared TIA-exported block."
            ),
        },
    }
