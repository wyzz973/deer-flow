import { afterEach, expect, it } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ComponentProps } from "react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ResearchComposer } from "@/components/deepresearch/research-conversation";

afterEach(cleanup);

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

function mount(props: Partial<ComponentProps<typeof ResearchComposer>>) {
  const calls = { sent: [] as string[], dismissed: 0, stopped: 0 };
  const view = render(
    <PromptInputProvider>
      <ResearchComposer
        status="RESEARCHING"
        welcome={false}
        busy={false}
        ready
        updating={false}
        onSend={async (text) => {
          calls.sent.push(text);
        }}
        onDismissQuote={() => calls.dismissed++}
        onStop={() => calls.stopped++}
        {...props}
      />
    </PromptInputProvider>,
  );
  const box = screen.getByLabelText<HTMLTextAreaElement>("研究消息");
  return { ...view, calls, box };
}

it("dismissing an update never sends the draft as a research update", async () => {
  const { calls, box } = mount({ updating: true, quote: "数据库选型调研" });
  fireEvent.change(box, { target: { value: "只关注并发（我又不想发了）" } });
  const dismiss = screen.getByRole("button", { name: "取消更新" });
  expect(dismiss.getAttribute("type")).toBe("button");
  fireEvent.click(dismiss);
  await sleep(50);
  expect(calls).toEqual({ sent: [], dismissed: 1, stopped: 0 });
  // The draft is the owner's to keep or send.
  expect(box.value).toBe("只关注并发（我又不想发了）");
  fireEvent.click(screen.getByRole("button", { name: "发送更新" }));
  await waitFor(() =>
    expect(calls.sent).toEqual(["只关注并发（我又不想发了）"]),
  );
});

it("stopping research is not a submit, and Enter cannot send while it runs", async () => {
  const { calls, box } = mount({});
  // Not an update the owner opened: the field really is unavailable.
  expect(box.disabled).toBe(true);
  expect(box.placeholder).toContain("更新");
  const stop = screen.getByRole("button", { name: "停止研究" });
  expect(stop.getAttribute("type")).toBe("button");
  fireEvent.click(stop);
  await sleep(50);
  expect(calls).toEqual({ sent: [], dismissed: 0, stopped: 1 });
});

it("refuses a keyboard submit in a state that takes no message, keeping the draft", async () => {
  // No submit button is rendered while the report is being written, so the
  // shared Enter handler has nothing to consult; the form handler must refuse.
  const { calls, box, container } = mount({ status: "RENDERING" });
  fireEvent.change(box, { target: { value: "补充一句" } });
  fireEvent.submit(container.querySelector("form")!);
  await sleep(50);
  expect(calls.sent).toEqual([]);
  expect(box.value).toBe("补充一句");
});

it("makes a stopped or failed conversation plainly unavailable", () => {
  const stopped = mount({ status: "CANCELLED" });
  expect(stopped.box.disabled).toBe(true);
  expect(stopped.box.placeholder).toBe("研究已停止：继续研究请新建研究");
  expect(
    screen.getByRole("button", { name: "发送消息" }).hasAttribute("disabled"),
  ).toBe(true);
  stopped.unmount();
  const failed = mount({ status: "FAILED" });
  expect(failed.box.disabled).toBe(true);
  expect(failed.box.placeholder).toBe("研究未完成：继续研究请新建研究");
  failed.unmount();
  // With a report already published, a stopped follow-up can be continued.
  const resumable = mount({ status: "CANCELLED", hasReport: true });
  expect(resumable.box.disabled).toBe(false);
  expect(resumable.box.placeholder).toBe("继续提问或调整研究…");
  resumable.unmount();
  // Only a failure that can be retried points at the retry.
  const retryable = mount({ status: "FAILED", retryable: true });
  expect(retryable.box.placeholder).toBe(
    "研究未完成：可在上方重试，或新建研究",
  );
  retryable.unmount();
});

it("takes messages for a new research, a plan under review and a finished report", async () => {
  for (const [props, label] of [
    [{ welcome: true, status: "" }, "发送研究请求"],
    [{ status: "AWAITING_PLAN_CONFIRMATION" }, "发送消息"],
    [{ status: "COMPLETED" }, "发送消息"],
  ] as const) {
    const { calls, box, unmount } = mount(props);
    expect(box.disabled).toBe(false);
    fireEvent.change(box, { target: { value: "  内容  " } });
    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(calls.sent).toEqual(["内容"]));
    unmount();
  }
});

it("does not send while a request is in flight or the service is not ready", async () => {
  for (const props of [{ busy: true }, { ready: false }]) {
    const { calls, box, container, unmount } = mount({
      status: "COMPLETED",
      ...props,
    });
    fireEvent.change(box, { target: { value: "追问" } });
    fireEvent.submit(container.querySelector("form")!);
    await sleep(50);
    expect(calls.sent).toEqual([]);
    expect(box.value).toBe("追问");
    unmount();
  }
});
