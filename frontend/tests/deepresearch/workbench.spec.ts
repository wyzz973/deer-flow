import { expect, test } from "@playwright/test";

test("plan editing, approval, report and source card", async ({ page }) => {
  await page.goto("/deepresearch-demo");
  await expect(page.getByRole("note")).toContainText("合成测试数据");
  await page.getByLabel("研究问题", { exact: true }).fill("研究研发平台的技术趋势与落地约束");
  await page.getByRole("button", { name: "生成研究计划" }).click();
  await expect(page.getByRole("heading", { name: "审核研究计划" })).toBeVisible();
  await page.getByRole("textbox", { name: "R1 研究目标" }).fill("专门分析行业变化与组织采用条件");
  await expect(page.getByRole("button", { name: "确认并开始研究" })).toBeDisabled();
  await page.getByRole("button", { name: "保存修改并重新审核" }).click();
  await expect(page.getByText("版本 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认并开始研究" }).click();
  await expect(page.getByRole("heading", { name: "参考资料", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看引用 1", exact: true }).first().click();
  await expect(page.getByRole("dialog", { name: "引用来源详情" })).toContainText("证据摘要");
  await page.getByRole("button", { name: "关闭来源详情" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "MD", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/research-.*\.md/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "参考资料", exact: true })).toBeVisible();
});
