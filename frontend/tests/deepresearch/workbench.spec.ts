import { expect, test, type Page } from "@playwright/test";

const backend = "http://127.0.0.1:8022/api/deepresearch";

async function submitQuestion(
  page: Page,
  text = "比较数据库的并发写入、运维和全文检索",
) {
  await expect(page.getByRole("note")).toContainText("合成测试数据");
  await page.getByRole("textbox", { name: "研究消息", exact: true }).fill(text);
  await page.getByRole("button", { name: "发送研究请求", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "编辑", exact: true }),
  ).toBeVisible();
  const url = new URL(page.url());
  return url.searchParams.get("run") ?? url.pathname.split("/").at(-1)!;
}

async function startResearch(page: Page) {
  await page.getByRole("button", { name: /^开始研究/ }).click();
  await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible();
}

for (const entry of ["/deepresearch-demo", "/workspace/deepresearch"]) {
  test(`conversation plan, citations, activity and trace: ${entry}`, async ({
    page,
  }, testInfo) => {
    await page.goto(entry);
    const id = await submitQuestion(page);
    await expect(
      page.getByRole("textbox", { name: "研究问题", exact: true }),
    ).toHaveCount(0);

    // Editing pauses the server-owned countdown and survives a reload.
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "编辑", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(
      page.getByRole("button", { name: "编辑", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");

    // A conversational revision is acknowledged and starts research at once.
    await page
      .getByRole("textbox", { name: "研究消息", exact: true })
      .fill("只关注并发与运维，优先官方资料");
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(
      page.getByText(
        "演示：已按你的要求调整计划——只关注并发与运维，优先官方资料",
      ),
    ).toBeVisible();
    await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible();
    // The report takes the plan's place: no plan card or folded strip remains.
    await expect(page.locator('[aria-label="研究计划"]')).toHaveCount(0);
    await expect(
      page.locator("summary").filter({ hasText: "计划已更新" }),
    ).toHaveCount(0);
    expect(page.url()).toContain(id);

    await page
      .getByRole("button", { name: "全屏阅读报告", exact: true })
      .click();
    const reader = page.locator("[data-research-report-reader]");
    const citation = reader
      .getByRole("button", { name: "查看引用 1", exact: true })
      .first();
    const evidenceId = await citation.getAttribute("data-evidence-id");
    // Hovering a citation floats the excerpt it was cited for.
    await citation.hover();
    await expect(
      page
        .locator("[data-slot=hover-card-content]")
        .filter({ hasText: "合成测试证据" }),
    ).toBeVisible();
    await citation.click();
    await expect(
      page.getByRole("tab", { name: "来源", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    const source = page.locator(`[data-source-id="${evidenceId}"]`);
    await expect(source).toContainText("合成测试证据");
    await expect(
      page.getByRole("heading", { name: "example.invalid", exact: true }),
    ).toBeVisible();
    await source
      .getByRole("button", { name: "回到正文引用 1", exact: true })
      .click();
    await expect(citation).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath("report-sources-desktop.png"),
    });

    await page.getByRole("button", { name: "导出报告", exact: true }).click();
    const reportDownload = page.waitForEvent("download");
    await page
      .getByRole("menuitem", { name: "导出到 Markdown", exact: true })
      .click();
    expect((await reportDownload).suggestedFilename()).toBe(
      `research-${id}.md`,
    );

    await page.getByRole("tab", { name: /^活动/ }).click();
    // The panel is headed by the plan title; the region keeps a fixed name.
    await expect(
      page.getByRole("region", { name: "研究活动", exact: true }),
    ).toBeVisible();
    // Cost and efficiency come from the recorded calls, with a raw export.
    await page.getByRole("tab", { name: "指标", exact: true }).click();
    await expect(
      page.getByText("工具调用", { exact: true }).locator(".."),
    ).toContainText("4");
    const metricsDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出指标", exact: true }).click();
    expect((await metricsDownload).suggestedFilename()).toBe(
      `research-${id}-metrics.jsonl`,
    );
    await page.getByRole("button", { name: "关闭报告", exact: true }).click();
    await page.getByRole("button", { name: "查看 Trace", exact: true }).click();
    // The inspector opens on the model-call audit; the demo runner never calls a model.
    await expect(page.getByTestId("llm-calls-panel")).toContainText(
      "还没有模型调用",
    );
    await page.getByRole("tab", { name: "Trace 时间线", exact: true }).click();
    await expect(page.locator('[aria-label="Trace 时间轴"]')).toBeVisible();
    await page
      .getByRole("textbox", { name: "搜索 Trace", exact: true })
      .fill("dispatch");
    await page.getByRole("button", { name: "dispatch", exact: true }).click();
    await expect(page.locator('[aria-label="Trace 事件详情"]')).toContainText(
      "research_units",
    );
    const traceDownload = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出 Trace JSONL", exact: true })
      .click();
    expect((await traceDownload).suggestedFilename()).toBe(
      `research-${id}-trace.jsonl`,
    );
    await page.reload();
    await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible();
    await page
      .getByRole("textbox", { name: "研究消息", exact: true })
      .fill("解释这份报告");
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(
      page.getByText("演示回复：解释这份报告。此内容仅验证对话流程。", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator('[aria-label="研究报告预览"]')).toHaveCount(1);
    // Budgets are per task: the explanation is a new task that searched
    // nothing, and the research it follows keeps its own closed record.
    const run = (await (await page.request.get(`${backend}/${id}`)).json()) as {
      usage: { tool_calls: number };
      usage_history: { tool_calls: number }[];
    };
    expect(run.usage.tool_calls).toBe(0);
    expect(run.usage_history.at(-1)?.tool_calls).toBe(4);
  });
}

test("server deadline survives reload and starts exactly once without a browser approval", async ({
  page,
}) => {
  await page.goto("/deepresearch-demo");
  const id = await submitQuestion(page, "自动启动与刷新恢复验收");
  const before = (await (
    await page.request.get(`${backend}/${id}`)
  ).json()) as { auto_start_at: string };
  await page.reload();
  await expect(
    page.getByRole("button", { name: "编辑", exact: true }),
  ).toBeVisible();
  const after = (await (await page.request.get(`${backend}/${id}`)).json()) as {
    auto_start_at: string;
  };
  expect(after.auto_start_at).toBe(before.auto_start_at);
  await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible({
    timeout: 60_000,
  });
  await page.reload();
  await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible();
  const run = (await (await page.request.get(`${backend}/${id}`)).json()) as {
    usage: { tool_calls: number };
  };
  expect(run.usage.tool_calls).toBe(4);
});

test("native workspace history and research details work on mobile", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace/deepresearch");
  const id = await submitQuestion(page, "移动端研究验收");
  await startResearch(page);
  await page.getByRole("button", { name: "来源与活动", exact: true }).click();
  const details = page.getByRole("dialog", { name: "研究详情", exact: true });
  await expect(details).toBeVisible();
  await expect(
    details.getByRole("region", { name: "研究活动", exact: true }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("research-details-mobile.png"),
  });
  await details
    .getByRole("button", { name: "关闭研究详情", exact: true })
    .click();
  await page.getByRole("button", { name: "全屏阅读报告", exact: true }).click();
  const reference = page
    .locator("[data-research-report-reader]")
    .getByRole("button", { name: "查看引用 1", exact: true })
    .first();
  await reference.click();
  // Leave the chip so its hover card cannot cover the drawer.
  await page.mouse.move(0, 0);
  await details
    .getByRole("button", { name: "回到正文引用 1", exact: true })
    .click();
  await expect(details).toBeHidden();
  await expect(reference).toBeInViewport();
  await page.getByRole("button", { name: "关闭报告", exact: true }).click();
  await page
    .getByRole("button", { name: "Toggle Sidebar", exact: true })
    .click();
  const sidebar = page.getByRole("dialog", { name: "Sidebar", exact: true });
  await sidebar.locator(`a[href="/workspace/deepresearch/${id}"]`).click();
  await expect(sidebar).toBeHidden();
  await expect(page.locator('[aria-label="研究报告预览"]')).toBeVisible();
});

test("workspace explains an unavailable research extension and prevents submit", async ({
  page,
}) => {
  let submissions = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/deepresearch"
    )
      submissions++;
  });
  await page.route("**/api/deepresearch/capabilities", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );
  await page.goto("/workspace/deepresearch");
  await expect(
    page.getByRole("alert").filter({ hasText: "尚未启用 DeepResearch" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "研究消息", exact: true })
    .fill("不能提交的请求");
  await expect(
    page.getByRole("button", { name: "发送研究请求", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "研究消息", exact: true })
    .press("Enter");
  await expect(
    page.getByRole("alert").filter({ hasText: "尚未启用 DeepResearch" }),
  ).toBeVisible();
  expect(submissions).toBe(0);
});
