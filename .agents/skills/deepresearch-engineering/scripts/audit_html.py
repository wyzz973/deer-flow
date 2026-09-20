#!/usr/bin/env python3
# ruff: noqa: E501 - markup and table rows read best unwrapped.
"""The audit of one DeepResearch run as a single self-contained HTML page.

``audit_run.py`` calls ``render_html`` with the same facts it writes to
``audit-<run>.json``; the page adds nothing of its own, it only shows them:
the timeline, every stage and node with its time, tokens and tool use, each
research step, the research rounds, search and provider behaviour, and the
tool calls one by one. Styles, charts (inline SVG) and the few lines of script
are inside the file, so it opens offline and on an intranet.
"""

from __future__ import annotations

import html
import json
import re

STAGE_NAMES = {
    "rewrite": "改写请求",
    "planner": "规划",
    "plan_review": "等待确认",
    "dispatch": "研究",
    "evidence_merge": "证据合并",
    "validator": "缺口检查",
    "supplement": "安排补研",
    "synthesis": "写作",
    "citation_binder": "绑定引用",
    "final_validator": "终检",
    "renderer": "渲染",
    "follow_up": "追问",
}
SEVERITY = {"high": "高", "medium": "中", "low": "低", "info": "提示"}

STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2330;--muted:#667085;--line:#e4e7ec;--accent:#2f6fed;--model:#3b74e6;--search:#12a594;--read:#69b34c;--fail:#e5484d;--wait:#f0b429;--idle:#d8dee9;--cached:#9db8f2;--high:#e5484d;--medium:#f0a020;--low:#6b8fd6;--info:#8a94a6;--zebra:#fafbfc}
@media (prefers-color-scheme:dark){:root{--bg:#0f1319;--card:#171c25;--ink:#e6e9ef;--muted:#98a2b3;--line:#2a3140;--accent:#7aa2ff;--model:#6b9bff;--search:#2cc7b4;--read:#8fd06f;--fail:#ff6b70;--wait:#f5c451;--idle:#2b3342;--cached:#3d5a99;--zebra:#1b212c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;-webkit-text-size-adjust:100%}
header.top{padding:28px 16px 8px}
.wrap{max-width:1240px;margin:0 auto;padding:0 16px}
h1{font-size:22px;margin:0 0 6px;line-height:1.35}
h2{font-size:17px;margin:0 0 12px}
h3{font-size:14px;margin:18px 0 8px;color:var(--muted);font-weight:600}
.query{font-size:15px;margin:6px 0 10px}
.meta{color:var(--muted);font-size:12.5px;word-break:break-all}
.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;color:#fff;background:var(--search);vertical-align:2px;margin-left:8px}
.badge.FAILED,.badge.CANCELLED{background:var(--fail)}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);margin-bottom:16px}
nav .wrap{display:flex;gap:4px;overflow-x:auto;padding-top:8px;padding-bottom:8px}
nav a{white-space:nowrap;color:var(--muted);text-decoration:none;padding:3px 10px;border-radius:6px;font-size:13px}
nav a:hover{background:var(--card);color:var(--ink)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 18px 16px;margin:0 0 16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:10px;margin:0 0 16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .v{font-size:21px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.25}
.kpi .l{color:var(--muted);font-size:12px}
.kpi .s{color:var(--muted);font-size:12px;margin-top:2px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;white-space:nowrap;background:var(--card);position:sticky;top:0}
td.n,th.n{text-align:right;white-space:nowrap}
.nw,td:first-child{white-space:nowrap}.report td:first-child{white-space:normal}
td .sub{display:block;white-space:normal;min-width:190px;color:var(--muted);font-size:12.5px}
.share{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;align-items:center;max-width:760px}
tbody tr:nth-child(even){background:var(--zebra)}
tr.bad td{background:color-mix(in srgb,var(--fail) 9%,transparent)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--idle);min-width:120px}
.bar span{display:block;height:100%}
.bar.tall{height:16px}
.seg-model{background:var(--model)}.seg-search{background:var(--search)}.seg-read{background:var(--read)}.seg-fail{background:var(--fail)}.seg-wait{background:var(--wait)}.seg-cached{background:var(--cached)}.seg-idle{background:var(--idle)}.seg-muted{background:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--muted);font-size:12px;margin:6px 0 10px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.finding{border-left:4px solid var(--info);padding:8px 12px;margin:0 0 8px;background:var(--zebra);border-radius:0 6px 6px 0}
.finding.high{border-color:var(--high)}.finding.medium{border-color:var(--medium)}.finding.low{border-color:var(--low)}
.finding .t{font-weight:600}.finding .c{color:var(--muted);font-size:12px;margin-left:6px}
.finding .e{color:var(--muted);font-size:12.5px;margin-top:2px;word-break:break-word}
.finding .a{font-size:12.5px;margin-top:4px}
details{margin:6px 0}
summary{cursor:pointer;font-weight:600;padding:4px 0}
summary small{font-weight:400;color:var(--muted);margin-left:8px}
.note{color:var(--muted);font-size:12.5px;margin:4px 0 10px}
.report{border-left:3px solid var(--accent);padding-left:14px}
.report h1{font-size:18px}.report h2{font-size:15px;margin-top:18px}.report h3{font-size:14px;color:var(--ink)}
.report table{margin:8px 0}.report pre{background:var(--zebra);padding:10px;border-radius:6px;overflow-x:auto}
.tools input[type=search]{padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);min-width:220px}
.tools label{color:var(--muted);font-size:13px;margin-left:12px}
svg text{fill:var(--muted);font-size:11px;font-family:inherit}
svg .lbl{fill:var(--ink)}
.gantt{min-width:900px}
footer{color:var(--muted);font-size:12px;padding:4px 16px 32px;text-align:center}
"""

SCRIPT = """
document.querySelectorAll('[data-filter]').forEach(function(box){
  var scope=document.getElementById(box.dataset.filter),text=box.querySelector('input[type=search]'),failed=box.querySelector('input[type=checkbox]');
  function apply(){var q=text.value.trim().toLowerCase();scope.querySelectorAll('tbody tr').forEach(function(row){
    var show=(!q||row.textContent.toLowerCase().indexOf(q)>=0)&&(!failed.checked||row.classList.contains('bad'));row.style.display=show?'':'none';});
    if(q||failed.checked)scope.querySelectorAll('details').forEach(function(d){d.open=true;});}
  text.addEventListener('input',apply);failed.addEventListener('change',apply);});
"""


def esc(value):
    return html.escape("" if value is None else str(value))


def num(value, unit=""):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        value = round(value, 1)
    if isinstance(value, (int, float)):
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.2f}M{unit}"
        if abs(value) >= 10_000:
            return f"{value / 1000:.1f}k{unit}"
    return f"{value}{unit}"


def pct(value):
    return "—" if value is None else f"{value:.0%}"


def ratio(part, whole):
    return part / whole if whole and part is not None else None


def clock(seconds):
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f} 秒" if seconds >= 10 else f"{seconds:.1f} 秒"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} 分 {rest:02d} 秒"


def offset(seconds):
    if seconds is None:
        return "—"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes:02d}:{rest:02d}"


def table(head, rows, numeric=(), classes=None):
    """``numeric`` are the right-aligned columns; cells are already escaped markup."""
    out = ["<div class='scroll'><table><thead><tr>"]
    out += [f"<th class='n'>{esc(name)}</th>" if index in numeric else f"<th>{esc(name)}</th>" for index, name in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for number, row in enumerate(rows):
        mark = f" class='{classes[number]}'" if classes and classes[number] else ""
        out.append(f"<tr{mark}>" + "".join(f"<td class='n'>{cell}</td>" if index in numeric else f"<td>{cell}</td>" for index, cell in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def bar(segments, total=None, tall=False):
    """A stacked bar; ``segments`` are (value, css class, title)."""
    total = total or sum(value or 0 for value, _, _ in segments) or 1
    spans = "".join(f"<span class='{name}' style='width:{max(0.0, (value or 0) / total * 100):.2f}%' title='{esc(title)}'></span>" for value, name, title in segments if value)
    return f"<div class='bar{' tall' if tall else ''}'>{spans}</div>"


def legend(items):
    return "<div class='legend'>" + "".join(f"<span><i class='{name}'></i>{esc(label)}</span>" for name, label in items) + "</div>"


def sparkline(values, ceiling, width=90, height=22):
    if not values or not ceiling:
        return ""
    if len(values) == 1:
        values = values * 2
    step = (width - 2) / (len(values) - 1)
    points = " ".join(f"{1 + index * step:.1f},{height - 2 - (value / ceiling) * (height - 4):.1f}" for index, value in enumerate(values))
    return f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' role='img'><title>上下文从 {num(values[0])} 长到 {num(values[-1])} Token</title><polyline points='{points}' fill='none' stroke='var(--model)' stroke-width='1.6'/></svg>"


def inline(text):
    text = esc(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href='\2' rel='noreferrer'>\1</a>", text)


def markdown_html(text):
    """Enough Markdown for an audit report: headings, lists, tables, fenced code, quotes."""
    out, lines, index = [], text.splitlines(), 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            block = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            out.append("<pre><code>" + esc("\n".join(block)) + "</code></pre>")
        elif line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[index + 1]):
            cells = lambda row: [cell.strip() for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]  # noqa: E731
            head, rows = cells(line), []
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(cells(lines[index]))
                index += 1
            out.append(table(head, [[inline(cell.replace("\\|", "|")) for cell in row] for row in rows]))
            continue
        elif re.match(r"^#{1,4} ", line):
            level = len(line.split(" ")[0])
            out.append(f"<h{level}>{inline(line[level + 1 :])}</h{level}>")
        elif re.match(r"^\s*([-*]|\d+\.) ", line):
            ordered = bool(re.match(r"^\s*\d+\. ", line))
            items = []
            while index < len(lines) and (re.match(r"^\s*([-*]|\d+\.) ", lines[index]) or (lines[index].startswith("  ") and lines[index].strip())):
                if re.match(r"^\s{2,}([-*]|\d+\.) ", lines[index]) and items:
                    items[-1] += "<br>· " + inline(re.sub(r"^\s*([-*]|\d+\.) ", "", lines[index]))
                elif re.match(r"^\s*([-*]|\d+\.) ", lines[index]):
                    items.append(inline(re.sub(r"^\s*([-*]|\d+\.) ", "", lines[index])))
                else:
                    items[-1] += " " + inline(lines[index].strip())
                index += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>")
            continue
        elif line.startswith(">"):
            out.append(f"<blockquote class='note'>{inline(line.lstrip('> '))}</blockquote>")
        elif line.strip():
            block = [line.strip()]
            while index + 1 < len(lines) and lines[index + 1].strip() and not re.match(r"^(#{1,4} |\||```|>|\s*([-*]|\d+\.) )", lines[index + 1]):
                index += 1
                block.append(lines[index].strip())
            out.append("<p>" + inline(" ".join(block)) + "</p>")
        index += 1
    return "\n".join(out)


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def gantt(timeline):
    if not timeline or not timeline.get("end"):
        return "<p class='note'>没有可画的时间线（该运行没有阶段或角色的时间记录）。</p>"
    end, left, right, width = timeline["end"], 190, 16, 1180
    scale = (width - left - right) / end
    roles = timeline["roles"]
    row, top = 20, 54
    height = top + row * len(roles) + 26

    def x(seconds):
        return left + seconds * scale

    parts = [f"<svg class='gantt' viewBox='0 0 {width} {height}' width='100%' role='img' aria-label='运行时间线'>"]
    step = next((size for size in (30, 60, 120, 300, 600, 900, 1800, 3600) if end / size <= 14), 7200)
    tick = 0
    while tick <= end:
        parts.append(f"<line x1='{x(tick):.1f}' y1='18' x2='{x(tick):.1f}' y2='{height - 22}' stroke='var(--line)'/><text x='{x(tick):.1f}' y='{height - 8}' text-anchor='middle'>{offset(tick)}</text>")
        tick += step
    palette = {"dispatch": "var(--search)", "synthesis": "var(--model)", "planner": "var(--wait)", "rewrite": "var(--wait)", "plan_review": "var(--idle)"}
    parts.append(f"<text class='lbl' x='8' y='38'>工作流阶段</text><rect x='{left}' y='24' width='{width - left - right}' height='20' fill='var(--idle)' opacity='.45' rx='3'/>")
    for stage in timeline["stages"]:
        span = max(1.5, (stage["end"] - stage["start"]) * scale)
        name = STAGE_NAMES.get(stage["name"], stage["name"])
        parts.append(
            f"<rect x='{x(stage['start']):.1f}' y='24' width='{span:.1f}' height='20' rx='3' fill='{palette.get(stage['name'], 'var(--muted)')}'><title>{esc(name)} {offset(stage['start'])}–{offset(stage['end'])}（{clock(stage['end'] - stage['start'])}）</title></rect>"
        )
        if span > 56:
            parts.append(f"<text x='{x(stage['start']) + 6:.1f}' y='38' style='fill:#fff'>{esc(name)} {clock(stage['end'] - stage['start'])}</text>")
    previous = None
    for index, role in enumerate(roles):
        y = top + index * row
        if role.get("round") and role["round"] != previous and role["node"] == "research":
            parts.append(
                f"<line x1='{x(role['start']):.1f}' y1='6' x2='{x(role['start']):.1f}' y2='{height - 22}' stroke='var(--wait)' stroke-dasharray='4 3'/><text x='{x(role['start']) + 4:.1f}' y='15' style='fill:var(--wait)'>补研第 {role['round']} 轮</text>"
            )
        if role["node"] == "research":
            previous = role.get("round") or 0
        label = role["unit_id"] if role["node"] == "research" else f"{role['node']} · {role['unit_id'] or ''}"
        parts.append(f"<text class='lbl' x='8' y='{y + 13}'>{esc(label[:28])}</text>")
        parts.append(
            f"<rect x='{x(role['start']):.1f}' y='{y + 2}' width='{max(1.5, (role['end'] - role['start']) * scale):.1f}' height='{row - 5}' rx='2' fill='var(--idle)'><title>{esc(label)} {offset(role['start'])}–{offset(role['end'])}（{clock(role['end'] - role['start'])}）</title></rect>"
        )
        for start, finish in role["model"]:
            parts.append(f"<rect x='{x(start):.1f}' y='{y + 2}' width='{max(0.8, (finish - start) * scale):.1f}' height='7' fill='var(--model)'/>")
        for start, finish, kind, answered in role["tools"]:
            colour = "var(--fail)" if not answered else "var(--search)" if kind == "search" else "var(--read)"
            parts.append(f"<rect x='{x(start):.1f}' y='{y + 10}' width='{max(0.8, (finish - start) * scale):.1f}' height='7' fill='{colour}'/>")
    parts.append("</svg>")
    return "<div class='scroll'>" + "".join(parts) + "</div>" + legend([("seg-model", "模型调用"), ("seg-search", "搜索"), ("seg-read", "阅读"), ("seg-fail", "失败的工具调用"), ("seg-idle", "角色运行区间（空白处在等工具或排队）")])


def histogram(labels, answered, failed):
    peak = max([a + f for a, f in zip(answered, failed, strict=True)] or [1]) or 1
    width, height, slot = 520, 150, 520 / max(1, len(labels))
    parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' style='max-width:560px' role='img' aria-label='搜索耗时分布'>"]
    for index, label in enumerate(labels):
        good, bad = answered[index] / peak * 100, failed[index] / peak * 100
        left = index * slot + 10
        parts.append(f"<rect x='{left:.1f}' y='{120 - good:.1f}' width='{slot - 20:.1f}' height='{good:.1f}' fill='var(--search)'><title>{esc(label)} 成功 {answered[index]} 次</title></rect>")
        parts.append(f"<rect x='{left:.1f}' y='{120 - good - bad:.1f}' width='{slot - 20:.1f}' height='{bad:.1f}' fill='var(--fail)'><title>{esc(label)} 失败 {failed[index]} 次</title></rect>")
        parts.append(
            f"<text x='{left + (slot - 20) / 2:.1f}' y='{116 - good - bad:.1f}' text-anchor='middle'>{answered[index] + failed[index] or ''}</text><text x='{left + (slot - 20) / 2:.1f}' y='138' text-anchor='middle'>{esc(label)}</text>"
        )
    return "".join(parts) + "</svg>"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def kpis(facts):
    summary = facts["summary"]
    time, tokens, tools, report, research = summary["time"], summary["tokens"], summary["tools"], summary["report"], summary["research"]
    search = sum(row["searches"] for row in facts["phases"])
    failed = sum(row["failed_searches"] for row in facts["phases"])
    lost = sum(row["lost_seconds"] for row in facts["tool_waits"])
    cards = [
        (clock(time.get("wall_seconds")), "总时长", f"活跃 {clock(time.get('active_seconds'))} · 等待 {clock(time.get('waiting_seconds'))}"),
        (clock(time.get("model_seconds")), "模型耗时（各调用之和）", f"工具 {clock(time.get('tool_seconds'))} · 排队 {clock(time.get('queue_seconds'))}"),
        (num(tokens.get("input")), "输入 Token", f"输出 {num(tokens.get('output'))} · 共 {summary['model_calls'].get('count')} 次模型调用"),
        (pct(tokens.get("cache_read_ratio")), "缓存命中", f"可复用前缀 {pct(tokens.get('prefix_reuse_ratio'))}"),
        (num(tools.get("count")), "工具调用", f"错误 {tools.get('errors')}（{pct(tools.get('error_rate'))}）· 供应商切换 {tools.get('failovers')}"),
        (num(search), "搜索", f"失败 {failed}（{pct(ratio(failed, search))}）· 成功 P50 {num(facts['search']['answered_p50_seconds'], 's')}"),
        (num(tools.get("reads")), "阅读", f"读到 {tools.get('pages_read')} 个页面"),
        (clock(lost) if lost else "0 秒", "被失败调用拖住的等待", "各步骤合计；一轮里要等最慢的工具"),
        (f"{research.get('planned_units')} + {research.get('supplement_units')}", "研究步骤（计划 + 补研）", f"失败 {research.get('failed_units')} · 证据池 {num(research.get('evidence_pool'))}"),
        (num(report.get("citations")), "引用", f"{num(report.get('characters'))} 字符 · {report.get('sections')} 章 · {report.get('cited_domains')} 个域名"),
        (f"{num(summary['cost'].get('total'))} {summary['cost'].get('currency') or ''}".strip(), "费用", "未配置单价时为空"),
    ]
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><div class='l'>{esc(label)}</div><div class='v'>{esc(value)}</div><div class='s'>{esc(note)}</div></div>" for value, label, note in cards) + "</div>"


def findings_html(facts):
    if not facts["findings"]:
        return "<p class='note'>规则没有命中任何异常。</p>"
    return "".join(
        f"<div class='finding {esc(item['severity'])}'><div><span class='t'>{esc(item['title'])}</span><span class='c'>{esc(SEVERITY.get(item['severity'], item['severity']))} · {esc(item['code'])}</span></div>"
        f"<div class='e'>{esc(item['evidence'] or '')}</div><div class='a'>{esc(item['advice'])}</div></div>"
        for item in facts["findings"]
    )


def phases_html(facts):
    started = {}
    for stage in (facts.get("timeline") or {}).get("stages") or []:
        started.setdefault(stage["name"], stage["start"])
    rows = sorted(facts["phases"], key=lambda row: started.get(row["phase"], 1e12))
    total = sum(row["seconds"] or 0 for row in rows) or 1
    body = [
        [
            f"<strong>{esc(STAGE_NAMES.get(row['phase'], row['phase']))}</strong> <span class='mono note'>{esc(row['phase'])}</span>",
            clock(row["seconds"]),
            bar([(row["seconds"], "seg-model", f"{(row['seconds'] or 0) / total:.0%}")], total),
            pct((row["seconds"] or 0) / total),
            row["runs"],
            row["model_calls"] or "—",
            clock(row["model_seconds"]) if row["model_calls"] else "—",
            num(row["input_tokens"]),
            num(row["output_tokens"]),
            pct(ratio(row["cache_read_tokens"], row["input_tokens"])),
            row["tool_calls"] or "—",
            row["tool_errors"] or ("—" if not row["tool_calls"] else 0),
            f"{row['searches']}（{row['failed_searches']}）" if row["searches"] else "—",
            f"{row['reads']}（{row['failed_reads']}）" if row["reads"] else "—",
        ]
        for row in rows
    ]
    note = "<p class='note'>阶段耗时是墙钟时间；“模型耗时”是该阶段各模型调用之和，并行时会超过阶段耗时。搜索、阅读括号里是失败次数。等用户确认计划、网关停机的时间不算在阶段里，见顶部的“等待”。</p>"
    return note + table(["阶段", "耗时", "占比", "", "次数", "模型调用", "模型耗时", "输入", "输出", "缓存命中", "工具调用", "工具错误", "搜索（失败）", "阅读（失败）"], body, numeric={1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13})


def nodes_html(facts):
    rows = facts["summary"]["breakdown"]["by_node"]
    speed = {row["node"]: row for row in facts["output_speed"]}
    if not rows:
        return "<p class='note'>这次运行没有模型调用记录（演示后端，或早于计量的运行）。</p>"
    peak = max((row.get("input_tokens") or 0) for row in rows) or 1
    body = []
    for row in rows:
        cached, asked = row.get("cache_read_tokens") or 0, row.get("input_tokens") or 0
        body.append(
            [
                f"<strong>{esc(row['node'])}</strong>",
                esc("、".join(row.get("models") or [])),
                row["model_calls"],
                clock(row["model_ms"] / 1000),
                f"{num((row['latency_ms'].get('p50') or 0) / 1000, 's')} / {num((row['latency_ms'].get('p95') or 0) / 1000, 's')}",
                num(asked),
                bar([(cached, "seg-cached", f"缓存命中 {num(cached)}"), (asked - cached, "seg-model", f"未命中 {num(asked - cached)}")], peak),
                num(row.get("output_tokens")),
                num(speed.get(row["node"], {}).get("output_tokens_per_second"), " tok/s"),
                f"{pct(row.get('cache_read_ratio'))} / {pct(row.get('prefix_reuse_ratio'))}",
                row.get("truncated"),
                row.get("retries"),
                row.get("model_errors"),
            ]
        )
    return legend([("seg-cached", "输入里命中缓存的部分"), ("seg-model", "未命中的输入")]) + table(
        ["节点", "模型", "调用", "模型耗时", "P50 / P95", "输入", "", "输出", "输出速度", "命中 / 可复用", "截断", "重试", "错误"], body, numeric={2, 3, 4, 5, 7, 8, 9, 10, 11, 12}
    )


def steps_html(facts):
    loops = {row["unit_id"]: row for row in facts["threads"] if row["node"] == "research"}
    waits = {row["unit_id"]: row for row in facts["tool_waits"] if row["node"] == "research"}
    searches = {row["unit_id"]: row for row in facts["search"]["by_step"]}
    rounds = {unit: row["round"] for row in facts.get("rounds") or [] for unit in row["units"]}
    ceiling = max((row.get("max_input") or 0 for row in loops.values()), default=0)
    longest = max((unit["seconds"] or 0 for unit in facts["process"]["units"]), default=0) or 1
    body, marks = [], []
    for unit in facts["process"]["units"]:
        loop, wait, search = loops.get(unit["unit_id"], {}), waits.get(unit["unit_id"], {}), searches.get(unit["unit_id"], {})
        model, waited, lost = loop.get("model_seconds") or 0, wait.get("wait_seconds_without_failures") or 0, wait.get("lost_seconds") or 0
        rest = max(0.0, (unit["seconds"] or 0) - model - waited - lost)
        body.append(
            [
                f"<strong>{esc(unit['unit_id'])}</strong><span class='sub'>{esc(unit.get('title') or '')}</span>",
                f"补研 {rounds[unit['unit_id']]}" if rounds.get(unit["unit_id"]) else "计划",
                clock(unit["seconds"]),
                bar([(model, "seg-model", f"模型 {clock(model)}"), (waited, "seg-search", f"等工具 {clock(waited)}"), (lost, "seg-fail", f"被失败调用拖住 {clock(lost)}"), (rest, "seg-muted", f"其他（排队、转换等）{clock(rest)}")], longest),
                clock(lost) if lost else "—",
                loop.get("turns", "—"),
                sparkline(loop.get("inputs") or [], ceiling),
                num(loop.get("input_tokens")),
                pct(loop.get("cache_read_ratio")),
                f"{search.get('searches', 0)}（{search.get('failed_searches', 0)}）",
                f"{search.get('reads', 0)}（{search.get('failed_reads', 0)}）",
                unit["findings"],
                unit["raw_evidences"],
                unit["open_questions"],
                esc(unit["stop_reason"] or unit["error_code"] or ""),
            ]
        )
        marks.append("bad" if unit["status"] == "FAILED" else "")
    head = ["步骤", "轮次", "耗时", "", "白等", "轮数", "上下文增长", "输入", "命中率", "搜索（失败）", "阅读（失败）", "发现", "原始证据", "未解问题", "提前收尾"]
    return (
        legend([("seg-model", "模型"), ("seg-search", "等工具（去掉失败后）"), ("seg-fail", "被失败的调用拖住"), ("seg-muted", "其他：排队、结果转换等")])
        + table(head, body, numeric={2, 4, 5, 7, 8, 9, 10, 11, 12, 13}, classes=marks)
        + "<p class='note'>“白等”把每一批并行工具里失败的调用换成该工具成功调用的中位耗时后重算；差值是这一步等失败调用（多数是超时）的时间。</p>"
    )


def rounds_html(facts):
    rows = facts.get("rounds") or []
    if not rows:
        return "<p class='note'>没有研究步骤。</p>"
    wall = sum(row["wall_seconds"] or 0 for row in rows) or 1
    asked = sum(row["input_tokens"] for row in rows) or 1
    sources = sum(row["cited_sources_first_seen"] or 0 for row in rows) or 1
    body = [
        [
            "计划的步骤" if row["round"] == 0 else f"补研第 {row['round']} 轮",
            len(row["units"]),
            clock(row["wall_seconds"]),
            pct((row["wall_seconds"] or 0) / wall),
            clock(row["estimated_wall_seconds_without_failed_calls"]),
            num(row["input_tokens"]),
            pct(row["input_tokens"] / asked),
            f"{row['tool_calls']}（{row['tool_errors']}）",
            f"{row['searches']} / {row['reads']}",
            row["findings"],
            num(row["cited_findings"]),
            row["open_questions"],
            num(row["evidence_first_seen"]),
            num(row["cited_evidence_first_seen"]),
            num(row["cited_sources_first_seen"]),
            pct((row["cited_sources_first_seen"] or 0) / sources) if row["cited_sources_first_seen"] is not None else "—",
        ]
        for row in rows
    ]
    shares = ""
    if len(rows) > 1:
        colours = ["seg-model", "seg-wait", "seg-search", "seg-read"]
        lines = [
            ("时间", [(row["wall_seconds"], colours[index % 4], f"第 {row['round']} 轮 {clock(row['wall_seconds'])}") for index, row in enumerate(rows)]),
            ("输入 Token", [(row["input_tokens"], colours[index % 4], f"第 {row['round']} 轮 {num(row['input_tokens'])}") for index, row in enumerate(rows)]),
            ("被引用的来源", [(row["cited_sources_first_seen"], colours[index % 4], f"第 {row['round']} 轮 {row['cited_sources_first_seen']}") for index, row in enumerate(rows)]),
        ]
        shares = "<h3>成本与产出的占比</h3>" + legend([(colours[index % 4], "计划的步骤" if row["round"] == 0 else f"补研第 {row['round']} 轮") for index, row in enumerate(rows)])
        shares += "<div class='share'>" + "".join(f"<span>{esc(name)}</span>{bar(parts, tall=True)}" for name, parts in lines if any(value for value, _, _ in parts)) + "</div>"
    note = "<p class='note'>证据和引用来源按“第一次读到它的轮次”归属；补研步骤会继承父步骤的证据，所以首轮读到、补研又用到的仍算首轮。“推算”是去掉失败调用造成的等待后，按同样的并发重排这一轮的时长。</p>"
    return (
        note
        + table(
            ["轮次", "步骤", "墙钟", "时间占比", "推算（无失败调用）", "输入", "Token 占比", "工具（错误）", "搜索 / 阅读", "发现", "被引用的发现", "未解问题", "首次读到的证据", "其中被引用", "首次读到的引用来源", "来源占比"],
            body,
            numeric=set(range(1, 16)),
        )
        + shares
    )


def cache_html(facts):
    tokens, cache = facts["summary"]["tokens"], facts["prompt_cache"]
    asked, cached = tokens.get("input") or 0, tokens.get("cache_read") or 0
    check, breaks = cache["provider_check"], cache["prefix_breaks"]
    out = [legend([("seg-cached", f"缓存命中 {num(cached)}"), ("seg-model", f"未命中的输入 {num(asked - cached)}"), ("seg-wait", f"输出 {num(tokens.get('output'))}")])]
    out.append(bar([(cached, "seg-cached", "缓存命中"), (asked - cached, "seg-model", "未命中"), (tokens.get("output"), "seg-wait", "输出")], tall=True))
    out.append(
        f"<p>供应商逐轮核对：<strong>{check['turns_served_from_cache']}/{check['turns_checked']}</strong> 个后续轮次的命中 Token ≥ 上一轮的完整输入。前缀断点：比较了 {breaks['turns_compared']} 个相邻请求，<strong>{breaks['breaks']}</strong> 处不是“只追加”。未上报用量的调用 {tokens.get('unreported_calls')} 次。</p>"
    )
    if breaks["examples"]:
        out.append(
            table(
                ["call_id", "节点", "步骤", "相同消息 / 上一轮", "疑似压缩", "原消息", "新消息"],
                [
                    [
                        f"<span class='mono'>{esc(row['call_id'])}</span>",
                        esc(row["node"]),
                        esc(row["unit_id"] or ""),
                        f"{row['shared_messages']}/{row['previous_messages']}",
                        "是" if row.get("likely_compaction") else "否",
                        esc(f"{row.get('previous_role')}: {row.get('previous_preview', '')}"),
                        esc(f"{row.get('role')}: {row.get('preview', '')}"),
                    ]
                    for row in breaks["examples"]
                ],
            )
        )
    loops = [row for row in facts["threads"] if row["turns"] >= 2]
    if loops:
        ceiling = max(row.get("max_input") or 0 for row in loops)
        out.append("<h3>Agent 循环（每轮整段重发上下文，命中率决定成本）</h3>")
        out.append(
            table(
                ["节点", "步骤", "轮数", "上下文增长", "首轮 → 末轮", "输入合计", "输出", "命中率", "可复用", "供应商逐轮命中", "模型耗时", "轮间（等工具）"],
                [
                    [
                        esc(row["node"]),
                        esc(row["unit_id"] or ""),
                        row["turns"],
                        sparkline(row.get("inputs") or [], ceiling),
                        f"{num(row['first_input'])} → {num(row['last_input'])}",
                        num(row["input_tokens"]),
                        num(row["output_tokens"]),
                        pct(row["cache_read_ratio"]),
                        pct(row["prefix_reuse_ratio"]),
                        f"{row['turns_served_from_cache']}/{row['turns_checked']}",
                        clock(row["model_seconds"]),
                        clock(row["between_turns_seconds"]),
                    ]
                    for row in loops[:30]
                ],
                numeric={2, 5, 6, 7, 8, 9, 10, 11},
            )
        )
    return "".join(out)


def tools_html(facts):
    tools, search = facts["summary"]["tools"], facts["search"]
    out = [
        table(
            ["工具", "角色", "次数", "错误", "重复", "P50 / P95", "返回字符"],
            [
                [
                    f"<strong>{esc(row['tool'])}</strong>",
                    esc(row.get("role") or ""),
                    row["count"],
                    row["errors"],
                    row.get("repeats"),
                    f"{num((row['latency_ms'].get('p50') or 0) / 1000, 's')} / {num((row['latency_ms'].get('p95') or 0) / 1000, 's')}",
                    num(row.get("output_chars")),
                ]
                for row in tools.get("by_tool", [])
            ],
            numeric={2, 3, 4, 5, 6},
        )
    ]
    if tools.get("by_provider"):
        peak = max(row["attempts"] + row["skipped"] + row["cached"] for row in tools["by_provider"]) or 1
        out.append("<h3>供应商（排在前面的先尝试；从未成功的供应商让每次调用先失败一轮）</h3>")
        out.append(legend([("seg-search", "有结果"), ("seg-wait", "空结果"), ("seg-fail", "错误"), ("seg-muted", "冷却中被跳过"), ("seg-cached", "读缓存")]))
        out.append(
            table(
                ["工具", "供应商", "", "尝试", "有结果", "空", "错误", "跳过", "缓存", "错误种类"],
                [
                    [
                        esc(row["tool"]),
                        f"<strong>{esc(row['provider'])}</strong>",
                        bar([(row["answered"], "seg-search", "有结果"), (row["empty"], "seg-wait", "空结果"), (row["errors"], "seg-fail", "错误"), (row["skipped"], "seg-muted", "跳过"), (row["cached"], "seg-cached", "缓存")], peak),
                        row["attempts"],
                        row["answered"],
                        row["empty"],
                        row["errors"],
                        row["skipped"],
                        row["cached"],
                        esc(json.dumps(row.get("error_kinds"), ensure_ascii=False)),
                    ]
                    for row in tools["by_provider"]
                ],
                numeric={3, 4, 5, 6, 7, 8},
                classes=["bad" if row["attempts"] >= 3 and not row["answered"] else "" for row in tools["by_provider"]],
            )
        )
    out.append(f"<h3>搜索耗时分布（成功 P50 {num(search['answered_p50_seconds'], ' 秒')} · P90 {num(search['answered_p90_seconds'], ' 秒')}）</h3>")
    out.append(legend([("seg-search", "成功"), ("seg-fail", "失败（贴着超时值的一柱就是超时）")]) + histogram(search["latency_buckets"], search["answered_searches"], search["failed_searches"]))
    if facts["failed_tool_time"]:
        out.append("<h3>失败的工具调用花掉的时间</h3>")
        out.append(
            table(
                ["工具", "失败次数", "合计", "平均", "错误类型"],
                [[esc(row["tool"]), row["calls"], clock(row["seconds"]), num(row["avg_seconds"], " 秒"), esc(json.dumps(row["error_types"], ensure_ascii=False))] for row in facts["failed_tool_time"]],
                numeric={1, 2, 3},
            )
        )
    if tools.get("error_types"):
        out.append("<p class='note'>错误类型：" + esc("、".join(f"{name} × {count}" for name, count in tools["error_types"].items())) + "</p>")
    if tools.get("failing_domains"):
        out.append("<h3>读取失败最多的站点</h3>")
        out.append(
            table(
                ["站点", "读取", "失败", "错误类型"],
                [[esc(row["domain"]), row["reads"], row["errors"], esc(json.dumps(row.get("error_types"), ensure_ascii=False))] for row in tools["failing_domains"]],
                numeric={1, 2},
            )
        )
    return "".join(out)


def tool_log_html(facts):
    groups = {}
    for row in facts.get("tool_log") or []:
        groups.setdefault(row["unit_id"] or "（无步骤）", []).append(row)
    if not groups:
        return "<p class='note'>这次运行没有工具调用。</p>"
    out = ["<div class='tools' data-filter='tool-log'><input type='search' placeholder='筛选：关键词、域名、供应商…' aria-label='筛选工具调用'><label><input type='checkbox'> 只看失败</label></div><div id='tool-log'>"]
    for unit, rows in groups.items():
        failed = sum(row["status"] == "error" for row in rows)
        out.append(f"<details><summary>{esc(unit)}<small>{len(rows)} 次调用 · 失败 {failed} · 合计 {clock(sum(row['seconds'] for row in rows))}</small></summary>")
        out.append(
            table(
                ["时刻", "工具", "状态", "耗时", "错误", "供应商尝试", "查询 / 地址", "返回字符"],
                [
                    [
                        offset(row["at"]),
                        f"<span class='nw'>{esc(row['tool'])}</span>",
                        "<span class='nw'>失败</span>" if row["status"] == "error" else "<span class='nw'>成功</span>",
                        num(row["seconds"], "s"),
                        esc(row["error_type"] or ""),
                        f"<span class='mono'>{esc(row['attempts'])}</span>",
                        esc(row["target"]),
                        num(row["output_chars"]),
                    ]
                    for row in rows
                ],
                numeric={0, 3, 7},
                classes=["bad" if row["status"] == "error" else "" for row in rows],
            )
        )
        out.append("</details>")
    return "".join(out) + "</div>"


def report_html(facts):
    summary, proc = facts["summary"], facts["process"]
    report, research, efficiency = summary["report"], summary["research"], summary["efficiency"]
    lines = [
        f"报告 {num(report.get('characters'))} 字符，{report.get('sections')} 章，{report.get('tables')} 张表，{report.get('diagrams')} 张图，{report.get('citations')} 条引用，来自 {report.get('cited_domains')} 个域名。",
        f"章节修复 {report.get('draft_repairs')} 次，删除陈述 {report.get('dropped_statements')} 条，终检重写 {report.get('validation_retries')} 次；转换重试 {research.get('conversion_retries')} 次；重做的模型调用 {sum(facts['repeat_calls'].values())} 次。",
        f"原始证据 {num(research.get('raw_evidence'))} 条，证据池 {num(research.get('evidence_pool'))} 条，裁剪 {research.get('trimmed_evidence')} 条。",
        f"效率：每条引用 {num(efficiency.get('tokens_per_citation'))} Token / {num(efficiency.get('active_seconds_per_citation'))} 秒；每读一页产出 {num(efficiency.get('citations_per_page_read'))} 条引用；每步搜索 {num(efficiency.get('searches_per_unit'))} 次。",
    ]
    if proc["gap_rounds"]:
        lines.append("触发补研的缺口：" + "；".join(json.dumps(item["codes"], ensure_ascii=False) for item in proc["gap_rounds"]))
    if proc["open_gaps"]:
        lines.append("结束时仍存在的缺口：" + "、".join(f"{gap['unit_id']}:{gap['code']}" for gap in proc["open_gaps"]))
    if proc["draft_repairs"]:
        lines.append("触发修复的写作任务：" + json.dumps(proc["draft_repairs"], ensure_ascii=False))
    if proc["search_limits"]:
        lines.append("被搜索预算收尾：" + "、".join(f"{item.get('unit_id')}（{item.get('used')}/{item.get('limit')}）" for item in proc["search_limits"] if item))
    out = "<ul>" + "".join(f"<li>{esc(line)}</li>" for line in lines) + "</ul>"
    out += "<p class='note'>脚本看不出报告有没有交付用户要的内容：对照研究请求逐项检查报告（要的表有没有、覆盖全不全），是审计者的工作。</p>"
    if facts["slowest_calls"]:
        out += "<h3>最慢的模型调用（用 show_call.py --call 打开）</h3>" + table(
            ["call_id", "节点", "步骤", "耗时", "输入", "命中", "输出", "tok/s", "结束原因"],
            [
                [
                    f"<span class='mono'>{esc(row['call_id'])}</span>",
                    esc(row["node"]),
                    esc(row["unit_id"] or ""),
                    num(row["seconds"], "s"),
                    num(row["input_tokens"]),
                    num(row["cache_read_tokens"]),
                    num(row["output_tokens"]),
                    num(row["output_tokens_per_second"]),
                    esc(row["finish_reason"] or row["status"]),
                ]
                for row in facts["slowest_calls"]
            ],
            numeric={3, 4, 5, 6, 7},
        )
    return out


def comparison_html(facts, baseline, rows):
    extra = facts.get("comparison") or {}
    out = ["<p class='note'>两次运行的计划通常不同：先比比例（命中率、每条引用成本、每次调用耗时、错误率），再比总量。</p>"]
    out.append("<h3>设置差异</h3>" + table(["设置", "基线", "本次"], [[esc(cell) for cell in row] for row in extra.get("settings_diff") or [["（没有差异，或其中一次早于设置快照）", "", ""]]]))
    out.append("<h3>指标</h3>" + table(["指标", f"基线 {baseline['identity']['run_id'][:8]}", "本次", "变化"], [[esc(cell) for cell in row] for row in rows], numeric={1, 2, 3}))
    if extra.get("same_question_runs"):
        out.append("<h3>同一问题的其他已完成运行（自然波动）</h3>")
        out.append(
            table(
                ["run", "创建", "步骤", "活跃秒", "模型调用", "输入", "命中率", "每步搜索", "工具错误率", "重做调用", "引用", "字符"],
                [
                    [
                        esc(row["run_id"][:8]),
                        esc((row["created_at"] or "")[:16]),
                        row["units"],
                        num(row["active_seconds"]),
                        row["model_calls"],
                        num(row["input_tokens"]),
                        pct(row["cache_read_ratio"]),
                        num(row["searches_per_unit"]),
                        pct(row["tool_error_rate"]),
                        row["repeat_calls"],
                        row["citations"],
                        num(row["characters"]),
                    ]
                    for row in extra["same_question_runs"]
                ],
                numeric=set(range(2, 12)),
            )
        )
    return "".join(out)


def render_html(facts, baseline=None, comparison_rows=None, report_markdown=None):
    identity = facts["identity"]
    short = identity["run_id"][:8]
    models = "、".join(row.get("key") or "" for row in facts["summary"]["breakdown"].get("by_model") or [])
    error = f"<p class='finding high'><strong>{esc((identity.get('error') or {}).get('code'))}</strong>：{esc((identity.get('error') or {}).get('message'))}</p>" if identity.get("error") else ""
    sections = [
        ("conclusion", "审计结论", "<div class='report'>" + markdown_html(report_markdown) + "</div>" if report_markdown else None),
        ("findings", "规则发现", "<p class='note'>规则命中只是线索；结论与根因需要审计者打开调用核实。</p>" + findings_html(facts)),
        ("timeline", "时间线", gantt(facts.get("timeline"))),
        ("phases", "各阶段", phases_html(facts)),
        ("nodes", "各节点", nodes_html(facts)),
        ("steps", "研究步骤", steps_html(facts)),
        ("rounds", "研究轮次与补研", rounds_html(facts)),
        ("cache", "Token 与缓存", cache_html(facts)),
        ("tools", "工具与搜索", tools_html(facts)),
        ("log", "工具调用明细", tool_log_html(facts)),
        ("report", "报告与过程", report_html(facts)),
        ("compare", "与基线对比", comparison_html(facts, baseline, comparison_rows or []) if baseline else None),
        (
            "settings",
            "本次运行的设置",
            "<details><summary>展开设置快照<small>凭据与提示词正文不在其中</small></summary><pre class='mono'>" + esc(json.dumps(facts["settings"], ensure_ascii=False, indent=1)) + "</pre></details>"
            if facts.get("settings")
            else "<p class='note'>该运行早于配置快照，设置以当时的运维配置为准。</p>",
        ),
    ]
    sections = [item for item in sections if item[2]]
    body = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>研究审计 {esc(short)}</title><style>{STYLE}</style></head><body>",
        f"<header class='top'><div class='wrap'><h1>DeepResearch 运行审计<span class='badge {esc(identity['status'])}'>{esc(identity['status'])}</span></h1>",
        f"<div class='query'>{esc(' '.join((identity.get('query') or '').split())[:400])}</div>",
        f"<div class='meta'>run <span class='mono'>{esc(identity['run_id'])}</span> · 创建 {esc((identity.get('created_at') or '')[:19])} · 模型 {esc(models or '—')} · 追问 {identity['follow_ups']} 次 · 已审计的模型请求 {identity['audited_requests']} 条<br>数据 <span class='mono'>{esc(identity['store'])}</span></div>{error}</div></header>",
        "<nav><div class='wrap'>" + "".join(f"<a href='#{key}'>{esc(title)}</a>" for key, title, _ in sections) + "</div></nav>",
        "<main class='wrap'>",
        kpis(facts),
    ]
    body += [f"<section id='{key}'><h2>{esc(title)}</h2>{content}</section>" for key, title, content in sections]
    body += ["</main>", f"<footer>由 audit_run.py 从研究数据库只读生成；同样的事实在 audit-{esc(short)}.json。页面含研究的查询与来源，按私有材料对待。</footer>", f"<script>{SCRIPT}</script></body></html>"]
    return "\n".join(body)
