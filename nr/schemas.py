"""JSON Schemas for every structured Claude output. The ordinal enums are the
labels the analysis ranks by, so their order matters (worst -> best)."""

COVERAGE = {"type": "string", "enum": ["searched_found", "searched_nothing_found",
                                       "inaccessible", "not_attempted"]}
CONTINUATION = ["strong_fade", "fade", "neutral", "continue", "strong_continue"]
NARRATIVE_POTENTIAL = ["none", "weak", "moderate", "strong"]
TOKEN_CONNECTION = ["none", "weak", "unclear", "moderate", "strong"]
FEASIBILITY = ["poor", "weak", "adequate", "good"]
AUTHENTICITY = ["coordinated", "strong_promotional", "insufficient_visibility",
                "unknown", "mixed", "independent_spread"]
CONFIDENCE = ["low", "medium", "high"]

_str = {"type": "string"}
_strs = {"type": "array", "items": _str}

REPORT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["what_is_happening", "narrative", "token_connection", "authenticity",
                 "market_feasibility", "counterargument", "unknowns", "source_coverage",
                 "thesis", "continuation_view", "research_confidence",
                 "observation_window", "sources"],
    "properties": {
        "what_is_happening": _str,
        "narrative": {
            "type": "object", "additionalProperties": False,
            "required": ["description", "stage", "potential", "why_spreading"],
            "properties": {
                "description": _str,
                "stage": {"type": "string", "enum": ["none_found", "nascent", "spreading",
                                                     "peaking", "fading", "unclear"]},
                "potential": {"type": "string", "enum": NARRATIVE_POTENTIAL},
                "why_spreading": _str,
            }},
        "token_connection": {
            "type": "object", "additionalProperties": False,
            "required": ["assessment", "why_this_token", "competing_tokens",
                         "is_first_or_canonical", "ticker_hijack_risk"],
            "properties": {
                "assessment": {"type": "string", "enum": TOKEN_CONNECTION},
                "why_this_token": _str,
                "competing_tokens": _str,
                "is_first_or_canonical": {"type": "string", "enum": ["yes", "no", "unclear"]},
                "ticker_hijack_risk": {"type": "string",
                                       "enum": ["low", "medium", "high", "unclear"]},
            }},
        "authenticity": {
            "type": "object", "additionalProperties": False,
            "required": ["assessment", "evidence", "uncertainty"],
            "properties": {
                "assessment": {"type": "string", "enum": AUTHENTICITY},
                "evidence": _str, "uncertainty": _str,
            }},
        "market_feasibility": {
            "type": "object", "additionalProperties": False,
            "required": ["assessment", "structural_risks"],
            "properties": {
                "assessment": {"type": "string", "enum": FEASIBILITY},
                "structural_risks": _strs,
            }},
        "counterargument": _str,
        "unknowns": _strs,
        "source_coverage": {
            "type": "object", "additionalProperties": False,
            "required": ["x_twitter", "reddit", "telegram", "discord", "news",
                         "project_site", "notes"],
            "properties": {"x_twitter": COVERAGE, "reddit": COVERAGE, "telegram": COVERAGE,
                           "discord": COVERAGE, "news": COVERAGE, "project_site": COVERAGE,
                           "notes": _str}},
        "thesis": _str,
        "continuation_view": {"type": "string", "enum": CONTINUATION},
        "research_confidence": {"type": "string", "enum": CONFIDENCE},
        "observation_window": {"type": "string", "enum": ["1h", "6h", "24h"]},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["url", "what"],
            "properties": {"url": _str, "what": _str}}},
    },
}

SKEPTIC = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strongest_bear_case", "red_flags", "bear_case_strength",
                 "what_would_refute_bear_case", "continuation_view",
                 "research_confidence", "coverage_notes", "sources"],
    "properties": {
        "strongest_bear_case": _str,
        "red_flags": _strs,
        "bear_case_strength": {"type": "string", "enum": ["weak", "moderate", "strong"]},
        "what_would_refute_bear_case": _str,
        "continuation_view": {"type": "string", "enum": CONTINUATION},
        "research_confidence": {"type": "string", "enum": CONFIDENCE},
        "coverage_notes": _str,
        "sources": REPORT["properties"]["sources"],
    },
}

POSTMORTEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["predictive_observations", "misleading_observations",
                 "evidence_that_should_have_changed_conclusion", "outcome_knowable",
                 "reasoning_quality", "hypotheses_proposed", "summary"],
    "properties": {
        "predictive_observations": _strs,
        "misleading_observations": _strs,
        "evidence_that_should_have_changed_conclusion": _strs,
        "outcome_knowable": {"type": "string",
                             "enum": ["knowable", "partially_knowable", "unknowable"]},
        "reasoning_quality": {"type": "string", "enum": ["poor", "fair", "good"]},
        "hypotheses_proposed": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["statement", "conditions", "market_structure_rationale"],
            "properties": {"statement": _str, "conditions": _str,
                           "market_structure_rationale": _str}}},
        "summary": _str,
    },
}


def validate(obj, schema) -> list[str]:
    """Minimal validator for the subset of JSON Schema used above."""
    errs: list[str] = []

    def walk(o, s, path):
        t = s.get("type")
        if t == "object":
            if not isinstance(o, dict):
                errs.append(f"{path}: expected object"); return
            for k in s.get("required", []):
                if k not in o:
                    errs.append(f"{path}.{k}: missing")
            for k, v in o.items():
                if k in s.get("properties", {}):
                    walk(v, s["properties"][k], f"{path}.{k}")
        elif t == "array":
            if not isinstance(o, list):
                errs.append(f"{path}: expected array"); return
            for i, v in enumerate(o):
                walk(v, s["items"], f"{path}[{i}]")
        elif t == "string":
            if not isinstance(o, str):
                errs.append(f"{path}: expected string")
            elif "enum" in s and o not in s["enum"]:
                errs.append(f"{path}: {o!r} not in {s['enum']}")
    walk(obj, schema, "$")
    return errs
