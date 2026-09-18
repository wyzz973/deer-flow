"""Agent execution adapters. Demo is explicit; real failures never use demo data."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import Field, ValidationError

from . import report as documents
from .config import FIXED_ROLES, active_settings
from .contracts import LIMITATION_LIMIT, RAW_EVIDENCE_LIMIT, Contract, Finding, FollowupResult, RawEvidence, ReportOutline, ResearchError, ResearchPlan, ResearchRequest, ResearchResult, ResearchUnit, SourceAnnotation
from .evidence import digest, ordered_sources
from .prompts import (  # noqa: F401 - historical import location for the default prompts
    CONVERSION_INSTRUCTIONS,
    OUTLINE_INSTRUCTIONS,
    PLANNER_INSTRUCTIONS,
    RESEARCH_INSTRUCTIONS,
    REVISION_INSTRUCTIONS,
    SECTION_INSTRUCTIONS,
    SUMMARY_INSTRUCTIONS,
)
from .report_policy import citable, eligible_evidence, section_count, section_target, source_allowed, source_roles, summary_target
from .trace import current_context
from .validators import single_source


class ResearchAnalysis(Contract):
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=2000)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    assumptions_needed: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)
    source_annotations: list[SourceAnnotation] = Field(default_factory=list, max_length=100)


class AgentRunner(Protocol):
    async def rewrite(self, run, message, previous=None) -> ResearchRequest: ...
    async def plan(self, run, proposed=None, request=None) -> ResearchPlan: ...
    async def research(self, run, unit, dependencies) -> ResearchResult: ...
    async def respond(self, run, text) -> FollowupResult: ...
    async def write_report(self, run, plan, results, findings, pool, limitations, errors=(), instruction=None) -> dict: ...
    async def revise_report(self, run, plan, previous, instruction, findings, pool) -> dict: ...


def today():
    return datetime.now(UTC).date().isoformat()


def bound_evidences(evidences, catalog, referenced, limit=RAW_EVIDENCE_LIMIT):
    """Keep a unit's result within its contract without losing what matters.

    Search results can mention hundreds of links. They only prove discovery and
    the source ledger records them separately, so surplus links go first, after
    superseded runtime envelopes whose raw output stays in the execution cache.
    Referenced evidence, read pages and tool records are kept.
    """
    if len(evidences) <= limit:
        return evidences, 0
    superseded = {item["raw_id"] for item in catalog if item.get("superseded")}

    def rank(index):
        item = evidences[index]
        if item.raw_id in referenced:
            return 0
        if item.provenance == "fetched_document":
            return 1
        if item.provenance == "observed_source":
            return 3
        return 4 if item.raw_id in superseded else 2

    keep = set(sorted(range(len(evidences)), key=lambda index: (rank(index), index))[:limit])
    return [item for index, item in enumerate(evidences) if index in keep], len(evidences) - limit


def result_error(exc: ValidationError):
    """Name a result contract failure without echoing model or tool content."""
    errors = exc.errors(include_input=False, include_url=False)
    if any("raw evidence" in str(error.get("msg", "")) for error in errors):
        return ResearchError("EVIDENCE_REFERENCE", "Findings refer to an unobserved native tool call")
    fields = sorted({".".join(str(part) for part in error.get("loc", ())) or str(error.get("type")) for error in errors})
    return ResearchError("RESULT_CONTRACT", "Research result exceeds its contract: " + ", ".join(fields[:5]))


def language_name(text):
    """Name the reader's language explicitly; models follow names better than codes."""
    return "Simplified Chinese (简体中文)" if documents.language(text) == "zh" else "English"


def user_updates(run):
    return [item["text"] for item in (run or {}).get("steering", [])]


def _site(url):
    try:
        return (urlsplit(url or "").hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def numbered_findings(findings, pool, eligible):
    """Stable finding IDs for the writer; citations keep only eligible evidence."""
    return [
        {
            "id": f"F{index:03d}",
            "unit_id": finding["unit_id"],
            "claim": finding["claim"],
            "evidence_ids": [eid for eid in finding.get("evidence_ids", []) if eid in eligible],
            "confidence": finding.get("confidence"),
            "high_risk": finding.get("high_risk", False),
            "single_source": single_source(finding, pool, eligible),
        }
        for index, finding in enumerate(findings, 1)
    ]


def evidence_catalog(pool, ids):
    catalog = {}
    for eid in ids:
        item = pool[eid]
        url = item.get("url") or item.get("canonical_url")
        catalog[eid] = {
            "title": (item.get("title") or "")[:300],
            "site": _site(url) or item.get("source_name"),
            "url": url,
            "published_at": item.get("published_at"),
            "kind": "page" if item.get("provenance") in {"fetched_document", "document"} else "tool record",
            "excerpt": documents.excerpt(item.get("snippet"), 600),
        }
    return catalog


def original_unit(units):
    """Map supplementary units back to the planned unit they extend."""
    mapping = {}
    for unit in units or []:
        mapping[unit["id"]] = unit["depends_on"][0] if unit.get("parent_gap_id") and unit.get("depends_on") else unit["id"]
    return mapping


class SettingsBound:
    """``settings`` is the executing run's configuration snapshot when one is bound."""

    @property
    def settings(self):
        return active_settings(self._settings)

    @settings.setter
    def settings(self, value):
        self._settings = value


class DemoRunner(SettingsBound):
    """Synthetic, deterministic integration fixture, not a search implementation."""

    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def rewrite(self, run, message, previous=None):
        if previous:
            return ResearchRequest(user_query=f"{previous}\n更新需求：{message}", acknowledgement="演示：已按你的要求调整计划——" + message)
        return ResearchRequest(user_query=f"{message}。请基于截至{today()}的公开资料深入研究，先给出结论，再说明依据与取舍。")

    async def plan(self, run, proposed=None, request=None):
        if proposed:
            if "revision" in proposed:
                revised = ResearchPlan.model_validate(proposed["plan"])
                revised.constraints.append(proposed["revision"])
                revised.clarification_questions = []
                revised.acknowledgement = "演示：已按你的要求调整计划——" + proposed["revision"]
                return revised
            return ResearchPlan.model_validate(proposed)
        names = list(self.settings.researchers())
        units = [
            ResearchUnit(id=f"R{i + 1}", skill=name, title=self.settings.skills[name].description[:24], objective=f"{run['query']}：{self.settings.skills[name].description}")
            for i, name in enumerate(names[: min(2, run["budget"]["max_units"])])
        ]
        brief = (request or {}).get("user_query") or "演示研究简报：" + run["query"]
        return ResearchPlan(goal=run["query"], title=run["query"][:40], brief=brief, constraints=run["constraints"], research_units=units)

    async def research(self, run, unit, dependencies):
        evidences = []
        for origin in ("internal", "external"):
            await self.store.reserve(run["run_id"], tool_calls=1)
            evidences.append(
                RawEvidence(
                    raw_id=f"{unit.id}-{origin}",
                    title=f"演示来源：{unit.skill} / {origin}",
                    url=f"https://example.invalid/demo/{unit.skill}" if origin == "external" else None,
                    source_uri=f"demo://internal/{unit.skill}" if origin == "internal" else None,
                    origin=origin,
                    source_name=f"demo-{origin}",
                    source_level="L2",
                    publisher=f"demo-{origin}",
                    snippet=f"合成测试证据，仅用于验证 {unit.objective} 的流程和引用，不代表真实研究结论。",
                )
            )
        import os

        from .sources import observed_sources

        delay = float(os.getenv("DEEPRESEARCH_DEMO_STEP_DELAY", "0"))
        for evidence in evidences:
            call_id = f"demo-{run.get('cycle', 0)}-{unit.id}-{evidence.origin}"
            details = {"tool_name": "demo_search", "unit_id": unit.id, "agent_name": self.settings.skills[unit.skill].agent, "started_at": evidence.retrieved_at, "status": "running", "demo": True}
            await self.store.record_call(run["run_id"], call_id, details)
            await self.store.event(run["run_id"], "activity.tool.started", {"call_id": call_id, **details})
            if delay:
                await asyncio.sleep(delay)
            found = observed_sources(evidence.url or "", connector=evidence.source_name, origin=evidence.origin)
            if found:
                evidence.source_id = found[0]["id"]
            call = await self.store.record_call(run["run_id"], call_id, {"status": "success", "ended_at": evidence.retrieved_at, "duration_ms": int(delay * 1000)}, found)
            await self.store.event(run["run_id"], "activity.tool.completed", call)
        return ResearchResult(
            unit_id=unit.id,
            raw_evidences=evidences,
            confidence=0.5,
            summary=f"演示：{unit.title or unit.objective} 已取得两条合成证据。",
            findings=[Finding(claim=f"演示发现：{unit.objective}。该内容只用于功能测试。", raw_evidence_refs=[e.raw_id for e in evidences], confidence=0.5)],
            searched_origins=["internal", "external"],
        )

    async def respond(self, run, text):
        # Synthetic fixture behavior only; the real runner classifies intent.
        action = "research" if "补充研究" in text else "revise" if "改写报告" in text else "answer"
        return FollowupResult(action=action, text="演示回复：" + text + "。此内容仅验证对话流程。")

    async def write_report(self, run, plan, results, findings, pool, limitations, errors=(), instruction=None):
        lang = documents.language(run.get("query", ""))

        def lines(unit_id):
            return [f"- {finding['claim']} [[{', '.join(finding['evidence_ids'])}]]" for finding in findings if finding["unit_id"] == unit_id and finding["evidence_ids"]]

        sections = [(unit.title or self.settings.skills[unit.skill].description, "\n".join(lines(unit.id)) or "该维度暂无有效证据。") for unit in plan.research_units]
        first = next((finding for finding in findings if finding["evidence_ids"]), None)
        summary = f"**{first['claim']}** [[{first['evidence_ids'][0]}]]" if first else "暂无有效证据。"
        if instruction:
            summary += "\n\n（演示：已按要求调整——" + instruction + "）"
        caveats = list(dict.fromkeys(limitations))[:5]
        title = "演示研究报告：" + run["query"][:180]
        document = documents.assemble(title, summary, sections, plan.assumptions, caveats, lang)
        return {"title": title, "document": document, "limitations": caveats, "assumptions": list(plan.assumptions), "audit": {"repairs": 0}}

    async def revise_report(self, run, plan, previous, instruction, findings, pool):
        document = previous["document"]
        lines = document.split("\n")
        marker = next((i for i, line in enumerate(lines) if line.startswith("## ")), 0)
        lines[marker + 1 : marker + 1] = ["", "（演示：已按要求改写——" + instruction + "）"]
        return {"title": previous.get("title") or documents.title_of(document), "document": "\n".join(lines), "limitations": previous.get("limitations", []), "assumptions": previous.get("assumptions", []), "audit": {"repairs": 0}}


class DeerFlowRunner(SettingsBound):
    """Uses this fork's Custom Agent config, SDK factory and MCP interceptors.

    All credentials travel on Runtime.context only. SDK children are ephemeral;
    only sanitized structured results enter the parent checkpoint/store.
    """

    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def _agent_config(self, skill_name):
        """The native subagent definition that executes a research role.

        A role bound to a DeerFlow subagent (``agent``) reuses its prompt, tools
        and model; a self-contained role is described entirely by its settings.
        Either way the native SubagentExecutor runs it.
        """
        settings = self.settings
        spec = settings.skills.get(skill_name)
        if spec is None or (not spec.enabled and skill_name not in FIXED_ROLES):
            raise ResearchError("SKILL_DENIED", f"研究角色未启用或不存在: {skill_name}", recoverable=False)
        if spec.agent is None:
            from deerflow.subagents.config import SubagentConfig

            fixed = skill_name in FIXED_ROLES
            agent = SubagentConfig(
                name="deepresearch-" + skill_name,
                description=spec.description,
                # execute_role composes the role prompt, methodology and guards.
                system_prompt=None,
                tools=spec.tools,
                disallowed_tools=["task"],
                skills=[],
                model=settings.default_model or "inherit",
                max_turns=spec.max_turns or (128 if fixed else 256),
                timeout_seconds=spec.timeout_seconds,
            )
            return spec, agent
        from deerflow.subagents.registry import get_subagent_config

        agent = await asyncio.to_thread(get_subagent_config, spec.agent)
        if agent is None:
            raise ResearchError("AGENT_NOT_CONFIGURED", f"未配置 Custom Agent: {spec.agent}", recoverable=False)
        if agent.skills is not None and skill_name not in agent.skills:
            raise ResearchError("SKILL_DENIED", f"Agent 未授权 Skill: {skill_name}", recoverable=False)
        return spec, agent

    async def _json(self, run, skill_name, payload, schema, tools=None, agent=None, *, validator=None, repair=None, task_id=None):
        # Keep the workflow contract separate from native agent execution.
        from .structured import run_structured

        return await run_structured(self, run, skill_name, payload, schema, tools, agent, validator=validator, repair=repair, task_id=task_id)

    async def rewrite(self, run, message, previous=None):
        """Rewrite the conversation into one complete research request.

        This is the step ChatGPT's conversation model performs before a deep
        research session starts (the ``user_query`` it sends): a direct model
        call without tools, whose output the planner and researchers share.
        """
        from .structured import rewrite_request

        settings = self.settings
        conversation = [{"role": m["role"], "text": m["text"]} for m in run.get("conversation", [])[-20:] if m.get("kind") in {None, "text", "clarification"}]
        payload = {
            "today": today(),
            "language": language_name(run["query"]),
            "conversation": conversation,
            "latest_user_message": message,
            "previous_request": previous,
            "current_plan": {"title": (run.get("plan") or {}).get("title"), "steps": [unit.get("title") or unit.get("objective") for unit in (run.get("plan") or {}).get("research_units", [])]} if previous and run.get("plan") else None,
            "available_sources": [{"name": s.name, "origin": s.origin, "role": s.role} for s in settings.sources],
        }
        return await rewrite_request(self, run, payload)

    async def plan(self, run, proposed=None, request=None):
        settings = self.settings
        request = request or {}
        names = {k: v.description for k, v in settings.researchers().items()}
        return await self._json(
            run,
            "deepresearch",
            {
                "research_request": request.get("user_query") or run["query"],
                "request_clarification_questions": request.get("clarification_questions", []),
                "original_user_message": run["query"],
                "today": today(),
                "language": language_name(run["query"]),
                "constraints": run["constraints"],
                "available_skills": names,
                "available_sources": [{"name": s.name, "origin": s.origin, "role": s.role} for s in settings.sources],
                "require_dual_source": settings.require_dual_source,
                "budget": run["budget"],
                "proposed_plan_to_normalize": proposed,
                "instructions": settings.prompts.plan,
            },
            ResearchPlan,
        )

    @asynccontextmanager
    async def _source_tools(self, run, sources):
        """Build the tools a unit's sources expose to its researcher.

        Provider-based sources become failover tools and ``kind: mcp`` sources
        on DeepResearch MCP servers expose that server's tool. Older files may
        still bind exact host tools (``kind: native``) or host MCP servers;
        those keep their original objects, schemas and interceptors unchanged.
        """
        from . import channels
        from .mcp import source_tool

        settings = self.settings
        # Credentials supplied with this request (request_secret_headers) belong to
        # one user; they reach the source tools and never the model or the store.
        request_secrets = current_context().get("secrets") or None
        legacy_mcp = [source for source in sources if source.kind == "mcp" and source.server not in settings.mcp_servers]
        loaded = []
        if legacy_mcp:
            from deerflow.mcp.cache import get_cached_mcp_tools

            loaded = await asyncio.to_thread(get_cached_mcp_tools)
        native = []
        if any(s.kind == "native" for s in sources):
            from deerflow.config import get_app_config
            from deerflow.tools import get_available_tools

            app_config = await asyncio.to_thread(get_app_config)
            native = await asyncio.to_thread(get_available_tools, include_mcp=False, include_upload_tool=False, app_config=app_config)
        selected = {}
        for source in sources:
            if source.kind == "channel":
                selected[source.name] = channels.build_tool(source, settings, run["run_id"], request_secrets)
                continue
            if source.kind == "mcp" and source.server in settings.mcp_servers:
                selected[source.name] = await source_tool(source, settings.mcp_servers, request_secrets)
                continue
            if source.kind == "native":
                if settings.native_tools is not None and source.tool not in settings.native_tools:
                    raise ResearchError("TOOL_DENIED", f"Native tool is outside the configured ceiling: {source.tool}", recoverable=False)
                tool = next((tool for tool in native if tool.name == source.tool), None)
            else:
                from deerflow.tools.mcp_metadata import get_mcp_source

                tool = next((tool for tool in loaded if tool.name == source.tool and (get_mcp_source(tool) or {}).get("server_name") == source.server), None)
            if tool is None:
                code = "NATIVE_TOOL_MISSING" if source.kind == "native" else "MCP_TOOL_MISSING"
                raise ResearchError(code, f"Host research tool is unavailable: {source.tool}", recoverable=False)
            selected[source.name] = tool
        yield selected

    async def research(self, run, unit, dependencies):
        from dataclasses import asdict

        from .native import execute_role
        from .observations import NativeExecution, ground_source_annotations, research_observations
        from .structured import convert_answer

        sources, priority_origin = await asyncio.to_thread(ordered_sources, self.settings, unit.source_strategy, run["source_names"])
        _, agent = await self._agent_config(unit.skill)
        allowed = set(agent.tools) if agent.tools is not None else {s.tool for s in sources}
        allowed -= set(agent.disallowed_tools or [])
        sources = [s for s in sources if s.tool in allowed]
        if not set(unit.source_strategy.required_origins).issubset({s.origin for s in sources}):
            raise ResearchError("TOOL_DENIED", "Agent tool policy does not cover the required research sources", recoverable=False)
        context = current_context()
        current = await self.store.get(run["run_id"])
        state = current or run
        plan_context = state.get("plan") or {}
        source_policy = plan_context.get("source_policy", {})
        query = state.get("query", "")
        payload = {
            "unit": unit.model_dump(mode="json"),
            "research_brief": plan_context.get("brief") or query,
            "research_goal": query,
            "today": today(),
            "language": language_name(query),
            "constraints": plan_context.get("constraints", []),
            "assumptions": plan_context.get("assumptions", []),
            "user_updates": user_updates(state),
            "dependencies": dependencies,
            "source_policy": source_policy,
            "shared_run_budget": run["budget"],
            "sources": [{"name": s.name, "tool": s.tool, "origin": s.origin, "role": s.role} for s in sources],
            "instructions": self.settings.prompts.research,
        }
        await self.store.event(run["run_id"], "research.source_policy", {"unit_id": unit.id, "policy": priority_origin, "sources": [s.name for s in sources]})
        # Cache a completed native execution before output conversion. A format
        # repair/retry therefore does not repeat a successful research session.
        # Individual tools are not replay-cached here: arbitrary MCP tools can
        # have side effects and their native semantics must remain intact.
        key = "native-unit:" + digest([run.get("cycle", 0), unit.model_dump(mode="json"), dependencies])
        cached = await self.store.cached(run["run_id"], key)
        if cached is not None:
            await self.store.event(run["run_id"], "metrics.cache_hit", {"kind": "native-unit", "unit_id": unit.id})
            execution = NativeExecution(**cached)
        else:
            async with self._source_tools(run, sources) as tool_map:
                execution = await execute_role(self.settings, self.store, run, unit.skill, payload, list(tool_map.values()), agent, context)
            await self.store.cache(run["run_id"], key, asdict(execution))
        evidences, catalog = research_observations(execution, sources)
        # A supplement receives its parent's findings. Their references must
        # travel with them, otherwise valid parent IDs look fabricated when
        # the child result is validated against only its own tool calls.
        by_id = {e.raw_id: e for e in evidences}
        for dependency, findings in dependencies.items():
            if dependency not in unit.depends_on:
                raise ResearchError("DEPENDENCY_EVIDENCE", "Undeclared research dependency", recoverable=False)
            saved = await self.store.completed_unit(run["run_id"], f"{run.get('cycle', 0)}:{dependency}")
            if saved is None or saved["findings"] != findings:
                raise ResearchError("DEPENDENCY_EVIDENCE", "Dependency findings do not match the saved research result", recoverable=False)
            referenced = {ref for finding in findings for ref in finding["raw_evidence_refs"]}
            for raw in saved["raw_evidences"]:
                if raw["raw_id"] in referenced and raw["raw_id"] not in by_id:
                    item = RawEvidence.model_validate(raw)
                    by_id[item.raw_id] = item
                    catalog.append({"raw_id": item.raw_id, "dependency_unit_id": dependency, "origin": item.origin, "title": item.title, "url": item.url, "source_uri": item.source_uri})
        evidences = list(by_id.values())
        roles = source_roles(self.settings)
        superseded = {item["raw_id"] for item in catalog if item.get("superseded")}
        eligible = {eid for eid, item in by_id.items() if eid not in superseded and source_allowed(item, source_policy) and citable(item, roles, self.settings.cite_search_results)}
        catalog_all, catalog = catalog, [item for item in catalog if item["raw_id"] in eligible]

        def validate_references(value):
            referenced = {ref for finding in value.findings for ref in finding.raw_evidence_refs}
            referenced.update(annotation.raw_id for annotation in value.source_annotations)
            unknown = referenced - eligible
            if unknown:
                raise ValueError("Unknown evidence IDs: " + ", ".join(sorted(unknown)[:20]) + ". Use exact raw_id values from observed_calls, including declared dependency evidence; omit claims without a listed ID.")

        pruned = {}

        def prune_references(value):
            # Final bounded attempt: remove what cannot be verified instead of
            # failing the run or guessing a replacement ID.
            findings = []
            for finding in value.findings:
                refs = [ref for ref in finding.raw_evidence_refs if ref in eligible]
                pruned["references"] = pruned.get("references", 0) + len(finding.raw_evidence_refs) - len(refs)
                if refs:
                    findings.append(finding.model_copy(update={"raw_evidence_refs": refs}))
                else:
                    pruned["findings"] = pruned.get("findings", 0) + 1
            annotations = [annotation for annotation in value.source_annotations if annotation.raw_id in eligible]
            return value.model_copy(update={"findings": findings, "source_annotations": annotations})

        analysis = await convert_answer(
            self,
            run,
            unit.skill,
            {
                **payload,
                # The researcher has already read the original responses.
                # Conversion needs identities/locators, not a second copy of
                # every tool body. Full evidence remains in the local store.
                "observed_calls": [{k: v for k, v in call.items() if k not in {"excerpt", "superseded"}} for call in catalog],
                "instructions": self.settings.prompts.conversion,
            },
            ResearchAnalysis,
            execution.answer,
            context,
            validator=validate_references,
            repair=prune_references,
        )
        if pruned:
            await self.store.event(run["run_id"], "research.output.pruned", {"unit_id": unit.id, **pruned})
        ground_source_annotations(evidences, analysis.source_annotations)
        if execution.stop_reason:
            analysis.limitations = [*analysis.limitations[: LIMITATION_LIMIT - 1], f"Native agent stopped before normal completion: {execution.stop_reason}"]
        origins = sorted({e.origin for e in evidences if e.origin != "runtime"})
        referenced = {ref for finding in analysis.findings for ref in finding.raw_evidence_refs}
        evidences, dropped = bound_evidences(evidences, catalog_all, referenced)
        if dropped:
            await self.store.event(run["run_id"], "research.evidence.trimmed", {"unit_id": unit.id, "dropped": dropped, "kept": len(evidences)})
        try:
            return ResearchResult(unit_id=unit.id, raw_evidences=evidences, searched_origins=origins, **analysis.model_dump(exclude={"source_annotations"}))
        except ValidationError as exc:
            raise result_error(exc) from None

    async def respond(self, run, text):
        report = run.get("report") or {}
        return await self._json(
            run,
            "deepresearch",
            {
                "message": text,
                "current_plan": run.get("plan"),
                "report": documents.MARKER.sub("", report["document"]) if report.get("document") else report.get("report"),
                "conversation": [{"role": m["role"], "text": m["text"]} for m in run.get("conversation", [])[-20:]],
                "instructions": self.settings.prompts.follow_up,
            },
            FollowupResult,
        )

    async def _markdown(self, run, payload, allowed, *, task_id, heading=None, whole_document=False):
        """Run a writer task that returns Markdown; repair, then sanitize."""
        from .native import execute_role

        _, agent = await self._agent_config("report-synthesis")
        context = current_context()
        request, audit, text = payload, {"repairs": 0}, ""
        for attempt in range(self.settings.max_synthesis_repairs + 1):
            execution = await execute_role(self.settings, self.store, run, "report-synthesis", request, None, agent, context, task_id=task_id)
            text = documents.clean_answer(execution.answer, heading, whole_document=whole_document)
            errors = documents.problems(text, allowed) if text else ["The answer is empty. Write the requested Markdown."]
            if not errors:
                break
            audit["repairs"] += 1
            await self.store.event(run["run_id"], "report.draft.repair", {"task": task_id, "attempt": attempt + 1, "problems": len(errors)})
            request = {
                **payload,
                "previous_draft": text[:80000],
                "validation_errors_to_fix": errors,
                "repair_instructions": self.settings.prompts.draft_repair,
            }
        text, sanitized = documents.sanitize(text, allowed)
        return text, {**audit, **sanitized}

    async def write_report(self, run, plan, results, findings, pool, limitations, errors=(), instruction=None):
        current = await self.store.get(run["run_id"]) or run
        eligible = eligible_evidence(pool, plan.source_policy, self.settings)
        numbered = numbered_findings(findings, pool, eligible)
        query = current.get("query", "")
        lang = documents.language(query)
        updates = user_updates(current) + ([instruction] if instruction else [])
        generation = current.get("report_retry_generation", 0)
        base = {"today": today(), "language": language_name(query), "user_request": query, "research_brief": plan.brief or plan.goal, "plan_title": plan.title or plan.goal, "report_style": plan.report_style, "user_updates": updates}
        summaries = {result["unit_id"]: result.get("summary", "") for result in results if result.get("summary")}
        assumptions = list(dict.fromkeys([*plan.assumptions, *[item for result in results for item in result.get("assumptions_needed", [])]]))[:12]
        low, high = section_count(plan.report_style, self.settings.max_report_sections)
        originals = [unit.id for unit in plan.research_units]
        origin_of = original_unit(current.get("units") or [])

        def coverage(value):
            covered = {unit_id for section in value.sections for unit_id in section.unit_ids}
            missing = [unit_id for unit_id in originals if unit_id not in covered]
            if missing:
                raise ValueError("Every original unit id must appear in some section's unit_ids; missing: " + ", ".join(missing))
            if len(value.sections) > self.settings.max_report_sections:
                raise ValueError(f"Use at most {self.settings.max_report_sections} sections.")
            generated = [section.heading for section in value.sections if documents.reserved_heading(section.heading)]
            if generated:
                raise ValueError(
                    "The executive summary, research scope/limitations and references are generated separately; remove these sections: "
                    + "; ".join(generated)
                    + ". Put action items and recommendations in a closing decision section instead."
                )

        def tidy(value):
            # Final bounded attempt: drop generated parts, keep their unit coverage.
            kept = [section.model_copy(update={"heading": documents.plain_heading(section.heading)}) for section in value.sections if not documents.reserved_heading(section.heading)]
            if not kept:
                return value
            covered = {unit_id for section in kept for unit_id in section.unit_ids}
            orphans = [unit_id for section in value.sections for unit_id in section.unit_ids if unit_id not in covered]
            if orphans:
                kept[-1] = kept[-1].model_copy(update={"unit_ids": list(dict.fromkeys([*kept[-1].unit_ids, *orphans]))[:64]})
            return value.model_copy(update={"sections": kept})

        outline_payload = {
            **base,
            "task": "outline",
            "original_units": [{"id": unit.id, "title": unit.title, "objective": unit.objective} for unit in plan.research_units],
            "unit_summaries": summaries,
            "findings": [{key: finding[key] for key in ("id", "unit_id", "claim", "high_risk", "single_source")} for finding in numbered],
            "assumptions": assumptions,
            "raw_limitations": list(limitations)[:120],
            "section_count": {"minimum": low, "maximum": high},
            "validation_errors_to_fix": list(errors),
            "instructions": self.settings.prompts.outline,
        }
        outline_key = "report-outline:" + digest([run.get("cycle", 0), generation, outline_payload])
        cached = await self.store.cached(run["run_id"], outline_key)
        if cached is not None:
            await self.store.event(run["run_id"], "metrics.cache_hit", {"kind": "report-outline"})
            outline = ReportOutline.model_validate(cached)
        else:
            outline = await self._json(run, "report-synthesis", outline_payload, ReportOutline, validator=coverage, repair=tidy, task_id="report-outline")
            await self.store.cache(run["run_id"], outline_key, outline.model_dump(mode="json"))
        outline = outline.model_copy(update={"sections": [section.model_copy(update={"heading": documents.plain_heading(section.heading)}) for section in outline.sections]})
        await self.store.event(run["run_id"], "report.outline.ready", {"title": outline.title, "sections": [section.heading for section in outline.sections]}, key="outline-" + outline_key[-24:])
        stated = outline.assumptions or assumptions[:6]
        headings = [{"heading": section.heading, "purpose": section.purpose} for section in outline.sections]
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        async def write_section(index, section):
            units = set(section.unit_ids) or set(originals)
            selected = [finding for finding in numbered if origin_of.get(finding["unit_id"], finding["unit_id"]) in units]
            ids = list(dict.fromkeys(eid for finding in selected for eid in finding["evidence_ids"]))
            payload = {
                **base,
                "task": "write_section",
                "report_title": outline.title,
                "section": section.model_dump(mode="json"),
                "all_sections": headings,
                "key_conclusions": outline.key_conclusions,
                "assumptions": stated,
                "findings": selected,
                "evidence": evidence_catalog(pool, ids),
                "length": section_target(plan.report_style),
                "instructions": self.settings.prompts.section,
            }
            key = "report-section:" + digest([outline_key, index, payload])
            queued = time.monotonic()
            async with semaphore:
                cached = await self.store.cached(run["run_id"], key)
                if cached is not None:
                    await self.store.event(run["run_id"], "metrics.cache_hit", {"kind": "report-section", "index": index})
                    return cached
                queued_ms = round((time.monotonic() - queued) * 1000)
                await self.store.event(run["run_id"], "report.section.started", {"index": index, "heading": section.heading, "queued_ms": queued_ms})
                body, audit = await self._markdown(run, payload, set(ids), task_id=f"report-section-{index}", heading=section.heading)
                value = {"heading": section.heading, "body": body, "audit": audit}
                await self.store.cache(run["run_id"], key, value)
                await self.store.event(run["run_id"], "report.section.completed", {"index": index, "heading": section.heading, "characters": len(body)}, key="section-" + key[-24:])
                return value

        outcomes = await asyncio.gather(*(write_section(index, section) for index, section in enumerate(outline.sections, 1)), return_exceptions=True)
        failure = next((outcome for outcome in outcomes if isinstance(outcome, BaseException)), None)
        if failure is not None:
            raise failure
        drafts = list(outcomes)
        cited = list(dict.fromkeys(eid for draft in drafts for eid in documents.marker_ids(draft["body"])))
        summary_payload = {
            **base,
            "task": "write_summary",
            "report_title": outline.title,
            "key_conclusions": outline.key_conclusions,
            "assumptions": stated,
            "section_drafts": [{"heading": draft["heading"], "body": draft["body"]} for draft in drafts],
            "evidence": evidence_catalog(pool, cited),
            "length": summary_target(plan.report_style),
            "instructions": self.settings.prompts.summary,
        }
        summary_key = "report-summary:" + digest([outline_key, summary_payload])
        summary = await self.store.cached(run["run_id"], summary_key)
        if summary is not None:
            await self.store.event(run["run_id"], "metrics.cache_hit", {"kind": "report-summary"})
        if summary is None:
            body, audit = await self._markdown(run, summary_payload, set(cited), task_id="report-summary")
            summary = {"body": body, "audit": audit}
            await self.store.cache(run["run_id"], summary_key, summary)
        totals = {}
        for audit in [summary["audit"], *[draft["audit"] for draft in drafts]]:
            for name, count in audit.items():
                totals[name] = totals.get(name, 0) + count
        caveats = outline.limitations[:5]
        document = documents.assemble(outline.title, summary["body"], [(draft["heading"], draft["body"]) for draft in drafts], stated, caveats, lang)
        return {"title": outline.title, "document": document, "limitations": caveats, "assumptions": stated, "audit": totals}

    async def revise_report(self, run, plan, previous, instruction, findings, pool):
        current = await self.store.get(run["run_id"]) or run
        eligible = eligible_evidence(pool, plan.source_policy, self.settings)
        document = previous["document"]
        numbered = numbered_findings(findings, pool, eligible)
        ids = [eid for eid in dict.fromkeys([*documents.marker_ids(document), *[eid for finding in numbered for eid in finding["evidence_ids"]]]) if eid in eligible]
        payload = {
            "task": "revise_report",
            "today": today(),
            "language": language_name(current.get("query", "")),
            "user_request": instruction,
            "user_updates": user_updates(current),
            "previous_report": document,
            "findings": numbered,
            "evidence": evidence_catalog(pool, ids),
            "instructions": self.settings.prompts.revision,
        }
        text, audit = await self._markdown(run, payload, set(ids), task_id="report-revision", whole_document=True)
        title = documents.title_of(text) or previous.get("title") or documents.title_of(document)
        if not documents.title_of(text):
            text = f"# {title}\n\n{text}"
        return {"title": title, "document": text, "limitations": previous.get("limitations", []), "assumptions": previous.get("assumptions", []), "audit": audit}
