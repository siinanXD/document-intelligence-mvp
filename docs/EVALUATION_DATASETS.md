# Public datasets for later evaluation tracks

Nothing in this file is vendored. SIN-75 ships only the synthetic
`retrieval-v1` corpus. Before adding any external collection, re-check the
licence, redistribution terms and size.

| Dataset | Purpose | Licence / redistribution (unverified until legal review) | Future track | Integrate now? |
|---|---|---|---|---|
| xPPU / Pick and Place Unit (TU Munich) | Public manufacturing plant with documentation used in automation research | Academic research artefact; redistribution depends on the specific release | Machine / process reasoning, component vocabulary | No — large, not a retrieval golden set |
| MMLongBench-Doc | Long-document VQA / retrieval-style questions | Research benchmark; typically research-use terms on the paper/release | Long-document retrieval, citation | No — size and terms not cleared |
| OmniDocBench | Document parsing quality across layouts | Research benchmark | Parser quality (not retrieval) | No |
| DocLayNet | Document layout annotation (IBM) | CDLA-Permissive-1.0 on the published release — re-verify | Layout / region provenance | No — hundreds of MB, layout not retrieval |
| DELP / SkeySpot | Key-spot / drawing lookup | Unclear public redistribution | Schematic lookup later | No |
| Dataset-P&ID / PID2Graph | P&ID graph extraction | Research releases; graph reconstruction is a later track | Connection / graph extraction | No |
| PLCOpen XML examples | PLC project interchange samples | PLCOpen materials are often publicly documented; still confirm the example pack | PLC variable extraction | No — not needed to score today's search |

The local 30–50 case golden corpus remains the required retrieval deliverable.
Generation-v1 reuses those same synthetic files and adds answerability labels;
it is not a second customer corpus. Do not download these collections in CI.

`evaluation/datasets/machine-intelligence-v1` is also synthetic and local (SIN-99).
It is original generator output, not xPPU or any vendor project. Keep xPPU and
other public plants out of CI.
