"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { researchApi } from "./api";
import { latestSnapshot, parseResearchEvent } from "./events";
import { terminal, type ResearchEvent, type Run } from "./types";

type Ticket = { token: symbol; generation: number };

export function useResearchConversation(initialRunId?: string, apiBase = "") {
  const api = useMemo(() => researchApi(apiBase), [apiBase]);
  const client = useQueryClient();
  const [runId, setRunId] = useState(initialRunId);
  const [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const selectedId = useRef(runId);
  selectedId.current = runId;
  const generation = useRef(0);
  const inflight = useRef<symbol | null>(null);
  const cursor = useRef({ id: runId, seq: 0 });
  const creation = useRef<{ text: string; key: string } | null>(null);
  const submission = useRef<{
    id: string;
    text: string;
    key: string;
    version?: number;
  } | null>(null);
  const runKey = useMemo(
    () => ["research-run", api.root, runId],
    [api.root, runId],
  );
  const sourcesKey = useMemo(
    () => ["research-sources", api.root, runId],
    [api.root, runId],
  );
  const cap = useQuery({
    queryKey: ["research-capabilities", api.root],
    queryFn: api.capabilities,
    retry: false,
  });
  const run = useQuery({
    queryKey: runKey,
    queryFn: async () => {
      const incoming = await api.get(runId!);
      return latestSnapshot(client.getQueryData<Run>(runKey), incoming);
    },
    enabled: Boolean(runId),
    retry: false,
  });
  const sources = useQuery({
    queryKey: sourcesKey,
    queryFn: () => api.sources(runId!),
    enabled: Boolean(runId),
    retry: false,
  });
  const status = run.data?.status;

  useEffect(
    () => () => {
      generation.current++;
      inflight.current = null;
    },
    [],
  );

  useEffect(() => {
    if (!runId || (status && terminal.has(status))) return;
    if (cursor.current.id !== runId) {
      cursor.current = { id: runId, seq: 0 };
      setEvents([]);
    }
    const stream = new EventSource(
      `${api.root}/${encodeURIComponent(runId)}/events?after=${cursor.current.seq}`,
      { withCredentials: true },
    );
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const reconcile = () => {
      if (selectedId.current !== runId) return;
      void client.invalidateQueries({ queryKey: runKey });
      void client.invalidateQueries({ queryKey: sourcesKey });
    };
    // A process restart can change durable status without emitting a new event.
    // Reconcile on reconnect even when the replay cursor has no new frames.
    stream.onopen = reconcile;
    stream.onerror = reconcile;
    stream.onmessage = (event: MessageEvent<string>) => {
      const item = parseResearchEvent(event.data);
      if (!item) {
        reconcile();
        return;
      }
      if (item.run_id !== selectedId.current || item.seq <= cursor.current.seq)
        return;
      cursor.current.seq = item.seq;
      if (!item.type.startsWith("trace."))
        setEvents((old) => [...old, item].slice(-300));
      // Fixed throttle: sustained output must not postpone visible progress.
      refresh ??= setTimeout(() => {
        refresh = undefined;
        reconcile();
      }, 150);
    };
    return () => {
      stream.close();
      if (refresh) clearTimeout(refresh);
    };
  }, [api.root, client, epoch, runId, runKey, sourcesKey, status]);

  useEffect(() => {
    if (status && terminal.has(status))
      void client.invalidateQueries({
        queryKey: ["research-history", api.root],
      });
  }, [api.root, client, runId, status]);

  const commitUrl = useCallback(
    (id?: string) => {
      const url = new URL(window.location.href);
      if (apiBase) {
        if (id) url.searchParams.set("run", id);
        else url.searchParams.delete("run");
      } else {
        url.pathname = id
          ? `/workspace/deepresearch/${encodeURIComponent(id)}`
          : "/workspace/deepresearch";
        url.search = "";
      }
      window.history.replaceState(null, "", url);
      selectedId.current = id;
      setRunId(id);
    },
    [apiBase],
  );

  useEffect(() => {
    if (!initialRunId) {
      const id = new URLSearchParams(window.location.search).get("run");
      if (id) setRunId(id);
    }
  }, [initialRunId]);

  const begin = useCallback((): Ticket => {
    if (inflight.current) throw new Error("请求正在处理，请稍候。");
    const token = Symbol("research-request");
    inflight.current = token;
    setBusy(true);
    setError("");
    return { token, generation: generation.current };
  }, []);
  const finish = useCallback((ticket: Ticket) => {
    if (inflight.current === ticket.token) {
      inflight.current = null;
      setBusy(false);
    }
  }, []);
  const save = useCallback(
    (value: Run) => {
      client.setQueryData<Run>(
        ["research-run", api.root, value.run_id],
        (previous) => latestSnapshot(previous, value),
      );
    },
    [api.root, client],
  );

  const action = useCallback(
    async (name: string, body: unknown = {}) => {
      if (!runId) return;
      const ticket = begin();
      try {
        const updated = await api.action(runId, name, body);
        if (ticket.generation === generation.current) {
          save(updated);
          setEpoch((value) => value + 1);
          void client.invalidateQueries({ queryKey: sourcesKey });
        }
        return updated;
      } catch (cause) {
        if (ticket.generation === generation.current) {
          setError(cause instanceof Error ? cause.message : String(cause));
          void client.invalidateQueries({ queryKey: runKey });
        }
        throw cause;
      } finally {
        finish(ticket);
      }
    },
    [api, begin, client, finish, runId, runKey, save, sourcesKey],
  );

  const send = useCallback(
    async (text: string) => {
      if (!cap.data?.ready) {
        const message = cap.error?.message ?? "研究服务尚未就绪，请稍候再试。";
        setError(message);
        throw new Error(message);
      }
      const id = runId;
      const ticket = begin();
      try {
        if (!id) {
          if (creation.current?.text !== text)
            creation.current = { text, key: crypto.randomUUID() };
          const pending = creation.current;
          const created = await api.create(
            { query: text, budget: cap.data?.budget_ceiling },
            pending.key,
          );
          if (creation.current === pending) creation.current = null;
          save(created);
          if (ticket.generation === generation.current)
            commitUrl(created.run_id);
        } else {
          // Retain the same identity after an uncertain response. A retry can
          // reconcile an already accepted message without executing it twice.
          if (
            submission.current?.id !== id ||
            submission.current.text !== text
          ) {
            submission.current = {
              id,
              text,
              key: crypto.randomUUID(),
              version: run.data?.plan?.plan_version,
            };
          }
          const pending = submission.current;
          const updated = await api.message(
            id,
            text,
            pending.key,
            pending.version,
          );
          if (submission.current === pending) submission.current = null;
          save(updated);
        }
        if (ticket.generation === generation.current)
          setEpoch((value) => value + 1);
      } catch (cause) {
        if (ticket.generation === generation.current) {
          setError(cause instanceof Error ? cause.message : String(cause));
          if (id) void client.invalidateQueries({ queryKey: runKey });
          setEpoch((value) => value + 1);
        }
        throw cause;
      } finally {
        void client.invalidateQueries({
          queryKey: ["research-history", api.root],
        });
        finish(ticket);
      }
    },
    [
      api,
      begin,
      cap.data?.budget_ceiling,
      cap.data?.ready,
      cap.error?.message,
      client,
      commitUrl,
      finish,
      run.data?.plan?.plan_version,
      runId,
      runKey,
      save,
    ],
  );

  const newResearch = useCallback(() => {
    // Old requests may still finish on the server and appear in history, but
    // must never navigate back over a newly selected conversation or its draft.
    generation.current++;
    inflight.current = null;
    creation.current = null;
    submission.current = null;
    cursor.current = { id: undefined, seq: 0 };
    setEvents([]);
    setBusy(false);
    setError("");
    commitUrl(undefined);
  }, [commitUrl]);

  return {
    api,
    run: run.data,
    runId,
    cap: cap.data,
    sources: sources.data,
    events,
    loading: Boolean(runId && run.isPending),
    busy,
    action,
    send,
    error: error || ((cap.error ?? run.error)?.message ?? ""),
    newResearch,
  };
}
