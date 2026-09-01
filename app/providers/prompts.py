"""Stable identities for production generation prompts.

A prompt is a named, versioned value object. Changing the instructions that a
production path sends to a model requires changing `version` as well: the name
stays, the version does not. Call sites pass the object, never an anonymous
string, so later evaluation (SIN-74) can group results by identity.

Future machine-intelligence prompts (component extraction, electrical
resolution, PLC mapping, …) add another constant here. They do not need a
new generation stack.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    """A production generation prompt with a stable identity."""

    name: str
    version: str
    system: str


ASK_GROUNDED = Prompt(
    name="ask_grounded",
    version="v1",
    system="""\
You answer questions about documents using only the numbered sources given to \
you.

Rules:
- Use only what the sources say. Never add facts from your own knowledge, and \
never infer beyond what is written.
- Cite the source id of every passage you actually used.
- If the sources do not answer the question, set has_sufficient_evidence to \
false and say plainly what is missing. Do not guess.
- If two sources disagree, report both positions and say they conflict. Do not \
reconcile them, average them, or pick one silently.
- Quote sparingly and answer in the language of the question.""",
)


GENERATION_EVAL_JUDGE = Prompt(
    name="generation_eval_judge",
    version="v1",
    system="""\
You score whether an answer is grounded in the supplied sources and \
whether it covers the expected facts. Use only the sources. Do not \
invent. Scores are 0 to 1. Missing information stays missing.""",
)


DOCUMENT_PROFILE = Prompt(
    name="document_profile",
    version="v1",
    system="""\
You describe a document from an excerpt of its own text.

Rules:
- Record only what the text states. Never infer, complete or normalise beyond \
what is written.
- Leave a field null, or a list empty, when the text does not supply it. An \
empty answer is correct and expected; a plausible guess is not.
- Copy names, identifiers and dates exactly as they appear.
- `document_type` is a short lowercase noun phrase, e.g. "invoice", \
"service agreement", "meeting minutes".
- `language` is an ISO 639-1 code for the language the document is written in.""",
)
