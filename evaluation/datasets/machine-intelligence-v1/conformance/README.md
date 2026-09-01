# SimaticML conformance sample (placeholder)

SIN-99 requires a legally cleared, anonymized TIA Portal / Openness
block export so SIN-93 does not co-evolve with the synthetic generator.

That file is **not in this repository yet**. Do not treat any XML under
`sources/plc/` as a TIA export. Those files are labelled synthetic.

When the owner supplies a cleared export:

1. Place it at `conformance/sample.xml`.
2. Fill `provenance.template.json` and rename it to `provenance.json`.
3. Keep Siemens namespaces and block/network structure; strip customer names.

Until then, SIN-93 must not claim TIA-export compatibility.
CI does not install TIA Portal.
