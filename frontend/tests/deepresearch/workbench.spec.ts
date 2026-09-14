import { expect, test } from "@playwright/test";

for (const entry of ["/deepresearch-demo", "/workspace/deepresearch"]) {
  test(`plan, citations and trace: ${entry}`, async ({ page }, testInfo) => {
    await page.goto(entry);
    await expect(page.getByRole("note")).toContainText("合成测试数据");
    await page
      .getByLabel("研究问题", { exact: true })
      .fill("研究研发平台的技术趋势与落地约束");
    await page.getByRole("button", { name: "生成研究计划" }).click();
    await expect(
      page.getByRole("heading", { name: "审核研究计划" }),
    ).toBeVisible();
    await page
      .getByRole("textbox", { name: "R1 研究目标" })
      .fill("专门分析行业变化与组织采用条件");
    await expect(
      page.getByRole("button", { name: "确认并开始研究" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "保存修改并重新审核" }).click();
    await expect(page.getByText("版本 2", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "确认并开始研究" }).click();
    await expect(
      page.getByRole("heading", { name: "参考资料", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "查看引用 1", exact: true })
      .first()
      .click();
    await expect(page.getByRole("dialog", { name: /演示来源/ })).toContainText(
      "证据摘要",
    );
    await page.getByRole("button", { name: "关闭来源详情" }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "MD", exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(/research-.*\.md/);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "参考资料", exact: true }),
    ).toBeVisible();
    await page
      .getByText("本地 Trace · 节点 / Agent / 模型 / 工具", { exact: true })
      .click();
    await expect(page.getByText("Trace ID:", { exact: false })).toBeVisible();
    await expect(
      page
        .locator("summary")
        .filter({ hasText: "workflow · workflow" })
        .first(),
    ).toBeVisible();
    if (entry.startsWith("/workspace")) {
      await expect(
        page.getByRole("link", { name: "DeepResearch", exact: true }).first(),
      ).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath("workspace-desktop.png"),
        fullPage: true,
      });
    }
    for (const card of await page
      .getByRole("button")
      .filter({ hasText: "演示来源：" })
      .all()) {
      expect(
        await card.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      ).toBe(true);
    }
    const traceDownload = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出完整 Trace JSONL", exact: true })
      .click();
    expect((await traceDownload).suggestedFilename()).toMatch(
      /research-.*-trace\.jsonl/,
    );
  });
}

test("workspace research is usable on mobile", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace/deepresearch");
  await expect(page.getByLabel("研究问题", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Toggle Sidebar" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("workspace-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("workspace explains when the research extension is unavailable", async ({
  page,
}) => {
  await page.route("**/api/deepresearch/capabilities", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );
  await page.goto("/workspace/deepresearch");
  await expect(
    page.getByRole("alert").filter({ hasText: "尚未启用 DeepResearch" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成研究计划" }),
  ).toBeDisabled();
});
