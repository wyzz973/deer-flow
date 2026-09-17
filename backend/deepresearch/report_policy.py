"""Deterministic citation eligibility and editorial targets.

Nothing here interprets an MCP business payload. Eligibility is derived from the
evidence provenance recorded after native execution, the operator-configured
source role and the user's plan-level source policy.
"""

from urllib.parse import urlsplit

from .contracts import SourcePolicy


def source_allowed(evidence, policy):
    policy = SourcePolicy.model_validate(policy or {})
    item = evidence.model_dump() if hasattr(evidence, "model_dump") else evidence
    url = item.get("url") or item.get("canonical_url")
    if policy.allowed_domains:
        host = (urlsplit(url or "").hostname or "").lower()
        if not any(host == domain or host.endswith("." + domain) for domain in policy.allowed_domains):
            return False
    if policy.require_original and item.get("provenance") not in {"document", "fetched_document"}:
        return False
    for prefix in policy.excluded_url_prefixes:
        candidate, excluded = urlsplit(url or ""), urlsplit(prefix)
        same_host = (candidate.hostname or "").removeprefix("www.") == (excluded.hostname or "").removeprefix("www.")
        path = excluded.path.rstrip("/")
        if same_host and (candidate.path == path or candidate.path.startswith(path + "/")):
            return False
    return True


def source_roles(settings):
    return {source.name: source.role for source in settings.sources}


def citable(evidence, roles, cite_search_results=False):
    """Whether evidence may back a report citation, independent of scope.

    A fetched page is an original read. A declared data tool's own output is a
    citable record. Search result bodies and links merely observed in any tool
    output only prove discovery, so they stay out of citations unless the
    operator declares that search returns complete documents. Undeclared runtime
    tools (file reads, shell, raw browser output) are working material, not
    sources; a runtime read linked to a page is registered as that page instead.
    """
    item = evidence.model_dump() if hasattr(evidence, "model_dump") else evidence
    provenance = item.get("provenance", "document")
    if provenance in {"document", "fetched_document"}:
        return True
    if provenance == "observed_source":
        return cite_search_results
    role = roles.get(item.get("source_name"))
    if role is None:
        return False
    return role != "search" or cite_search_results


def eligible_evidence(pool, policy, settings):
    roles = source_roles(settings)
    return {eid for eid, evidence in pool.items() if source_allowed(evidence, policy) and citable(evidence, roles, settings.cite_search_results)}


# Per-section character targets. They guide the writer and trigger no failure:
# a long, well-cited analysis is better than a truncated or rejected report.
SECTION_TARGETS = {"brief": (500, 1200), "standard": (1200, 3000), "detailed": (2200, 5200)}
SUMMARY_TARGETS = {"brief": (300, 700), "standard": (600, 1400), "detailed": (900, 2000)}
SECTION_COUNTS = {"brief": (3, 4), "standard": (5, 7), "detailed": (6, 9)}


def section_target(style):
    low, high = SECTION_TARGETS[style]
    return {"target_characters": low, "soft_maximum_characters": high}


def summary_target(style):
    low, high = SUMMARY_TARGETS[style]
    return {"target_characters": low, "soft_maximum_characters": high}


def section_count(style, ceiling):
    low, high = SECTION_COUNTS[style]
    return min(low, ceiling), min(high, ceiling)
