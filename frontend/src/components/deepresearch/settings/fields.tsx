"use client";

import { createContext, useContext, useEffect, useId, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { referenceProblem, sameValue } from "@/core/deepresearch/settings";
import type { SecretStatus } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

/** Input that exists only on screen. A number or JSON field keeps text the
 * draft cannot hold (out of range, not yet valid JSON); the draft then still
 * has the previous value, so saving would silently store something other than
 * what the field shows. Fields report such text here and the page refuses to
 * save until it is fixed. Outside a provider nothing is reported. */
export const InvalidFields = createContext<
  (id: string, problem: string | null) => void
>(() => undefined);

/** Names the card a field sits in ("模型 flash"), so a reported problem can be found. */
export const FieldScope = createContext("");

function useInvalidReport(label: string, problem: string) {
  const id = useId();
  const report = useContext(InvalidFields);
  const scope = useContext(FieldScope);
  useEffect(() => {
    if (!problem) return;
    report(id, `${scope ? `${scope} · ` : ""}${label}：${problem}`);
    return () => report(id, null);
  }, [id, label, problem, report, scope]);
}

/** Where a header or environment value may take a credential from. */
export const CREDENTIAL_HINT = (
  <>
    值可以是 <code>$ENV_NAME</code>、<code>secret:NAME</code>
    ，也可以在字符串里插值：<code>{"Bearer ${ENV_NAME}"}</code>、
    <code>{"sid=${secret:kb-cookie}; lang=zh"}</code>
    。不要直接粘贴明文凭据，保存时会被拒绝。
  </>
);

export function Field({
  label,
  hint,
  children,
  className,
  htmlFor,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && (
        <p className="text-muted-foreground text-xs leading-5">{hint}</p>
      )}
    </div>
  );
}

export function TextField({
  label,
  hint,
  value,
  onChange,
  disabled,
  placeholder,
  className,
  mono,
}: {
  label: string;
  hint?: React.ReactNode;
  value: string | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  mono?: boolean;
}) {
  const id = useId();
  return (
    <Field label={label} hint={hint} className={className} htmlFor={id}>
      <Input
        id={id}
        value={value ?? ""}
        placeholder={placeholder}
        disabled={disabled}
        className={cn(mono && "font-mono text-xs")}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

/** A number typed as text, so partial input such as "0." survives; the draft only
 * receives valid values. Pass `step={1}` for an integer setting. */
export function NumberField({
  label,
  hint,
  value,
  onChange,
  disabled,
  min,
  max,
  step,
  nullable,
  placeholder,
  className,
}: {
  label: string;
  hint?: React.ReactNode;
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
  nullable?: boolean;
  /** What an empty nullable field means, e.g. "继承". */
  placeholder?: string;
  className?: string;
}) {
  const id = useId();
  const [local, setLocal] = useState<{
    text: string;
    value: number | null;
  } | null>(null);
  const current = value ?? null;
  // Local text is shown only while it still describes the draft value.
  const own = local !== null && local.value === current;
  const text = own ? local.text : (current?.toString() ?? "");
  const parsed = text.trim() === "" ? null : Number(text);
  const problem =
    parsed === null
      ? nullable
        ? ""
        : "请填写数字"
      : !Number.isFinite(parsed)
        ? "请填写数字"
        : (min !== undefined && parsed < min) ||
            (max !== undefined && parsed > max)
          ? `范围 ${min ?? "-∞"} – ${max ?? "∞"}`
          : step !== undefined &&
              Number.isInteger(step) &&
              !Number.isInteger(parsed)
            ? "请填写整数"
            : "";
  useInvalidReport(label, own ? problem : "");
  return (
    <Field
      label={label}
      hint={
        own && problem ? (
          <span className="text-destructive">{problem}</span>
        ) : (
          hint
        )
      }
      className={className}
      htmlFor={id}
    >
      <Input
        id={id}
        type="text"
        inputMode={
          step !== undefined && !Number.isInteger(step) ? "decimal" : "numeric"
        }
        value={text}
        disabled={disabled}
        placeholder={nullable ? (placeholder ?? "不限 / 默认") : undefined}
        aria-invalid={Boolean(own && problem)}
        // Valid text is normalized on blur; invalid text stays until it is
        // fixed, because it is what blocks the save.
        onBlur={() => {
          if (!problem) setLocal(null);
        }}
        onChange={(event) => {
          const next = event.target.value;
          const number = next.trim() === "" ? null : Number(next);
          const valid =
            number === null
              ? Boolean(nullable)
              : Number.isFinite(number) &&
                (min === undefined || number >= min) &&
                (max === undefined || number <= max) &&
                !(
                  step !== undefined &&
                  Number.isInteger(step) &&
                  !Number.isInteger(number)
                );
          // Invalid text stays on screen, bound to the unchanged draft value.
          setLocal({ text: next, value: valid ? number : current });
          if (valid && number !== current) onChange(number);
        }}
      />
    </Field>
  );
}

export function SwitchField({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: React.ReactNode;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
      <div className="space-y-1">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint && (
          <p className="text-muted-foreground text-xs leading-5">{hint}</p>
        )}
      </div>
      <Switch
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </div>
  );
}

export function SelectField({
  label,
  hint,
  value,
  options,
  onChange,
  disabled,
  className,
  placeholder,
}: {
  label: string;
  hint?: React.ReactNode;
  value: string | null | undefined;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
}) {
  return (
    <Field label={label} hint={hint} className={className}>
      <Select
        value={value ?? undefined}
        onValueChange={onChange}
        disabled={disabled}
      >
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue placeholder={placeholder ?? "请选择"} />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

const STATUS_BADGE: Record<
  SecretStatus | "unsaved",
  { label: string; className: string }
> = {
  set: {
    label: "已设置",
    className: "border-emerald-500/40 text-emerald-600 dark:text-emerald-400",
  },
  missing: {
    label: "未找到值",
    className: "border-destructive/50 text-destructive",
  },
  none: { label: "未填写", className: "text-muted-foreground" },
  unsaved: { label: "保存后检查", className: "text-muted-foreground" },
};

/** A credential reference ($ENV or secret:NAME) with its resolution status; never a literal key. */
export function SecretRefField({
  label,
  hint,
  value,
  onChange,
  disabled,
  statuses,
  saved,
  suggestedEnv,
}: {
  label: string;
  hint?: React.ReactNode;
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  statuses: Record<string, SecretStatus>;
  saved: string[];
  suggestedEnv?: string | null;
}) {
  const id = useId();
  const problem = referenceProblem(value);
  const status: SecretStatus | "unsaved" = !value
    ? "none"
    : (statuses[value] ?? "unsaved");
  const badge = STATUS_BADGE[status];
  return (
    <Field
      label={label}
      htmlFor={id}
      hint={
        problem ? (
          <span className="text-destructive">{problem}</span>
        ) : (
          (hint ?? (
            <>
              写 <code>$环境变量名</code>（网关进程环境或 .env）或{" "}
              <code>secret:名字</code>（在“密钥与历史”中保存）。
              {suggestedEnv && (
                <>
                  {" "}
                  常用变量：<code>${suggestedEnv}</code>
                </>
              )}
            </>
          ))
        )
      }
    >
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={value ?? ""}
          disabled={disabled}
          placeholder={
            suggestedEnv ? `$${suggestedEnv}` : "$API_KEY 或 secret:名字"
          }
          className="font-mono text-xs"
          list={`${id}-saved`}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => onChange(event.target.value.trim() || null)}
        />
        <datalist id={`${id}-saved`}>
          {saved.map((name) => (
            <option key={name} value={`secret:${name}`} />
          ))}
        </datalist>
        <Badge variant="outline" className={cn("shrink-0", badge.className)}>
          {badge.label}
        </Badge>
      </div>
    </Field>
  );
}

/** Multi-line text such as a prompt or methodology, edited verbatim. */
export function LongTextField({
  label,
  hint,
  value,
  onChange,
  disabled,
  minRows = 6,
  maxRows = 28,
  headerRight,
}: {
  label: string;
  hint?: React.ReactNode;
  value: string | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
  minRows?: number;
  maxRows?: number;
  headerRight?: React.ReactNode;
}) {
  const id = useId();
  const text = value ?? "";
  const rows = Math.min(
    maxRows,
    Math.max(minRows, text.split("\n").length + 1),
  );
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <span className="text-muted-foreground text-xs">
          {text.length.toLocaleString()} 字符
        </span>
        <div className="ml-auto flex items-center gap-1">{headerRight}</div>
      </div>
      <Textarea
        id={id}
        value={text}
        rows={rows}
        disabled={disabled}
        spellCheck={false}
        className="font-mono text-xs leading-5"
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && (
        <p className="text-muted-foreground text-xs leading-5">{hint}</p>
      )}
    </div>
  );
}

/** A JSON object edited as text; invalid JSON stays on screen until it parses. */
export function JsonField({
  label,
  hint,
  value,
  onChange,
  disabled,
  nullable,
}: {
  label: string;
  hint?: React.ReactNode;
  value: unknown;
  onChange: (value: Record<string, unknown> | null) => void;
  disabled?: boolean;
  nullable?: boolean;
}) {
  const id = useId();
  const [local, setLocal] = useState<{
    text: string;
    value: unknown;
    error: string;
  } | null>(null);
  // Keep the author's formatting while it still describes the draft value;
  // an outside change (discard, reset, restore) shows the new value instead.
  const own = local !== null && sameValue(local.value ?? null, value ?? null);
  const text = own
    ? local.text
    : value == null
      ? ""
      : JSON.stringify(value, null, 2);
  const error = own ? local.error : "";
  useInvalidReport(label, error);
  return (
    <Field
      label={label}
      htmlFor={id}
      hint={error ? <span className="text-destructive">{error}</span> : hint}
    >
      <Textarea
        id={id}
        value={text}
        disabled={disabled}
        spellCheck={false}
        aria-invalid={Boolean(error)}
        rows={Math.min(14, Math.max(3, text.split("\n").length))}
        placeholder={nullable ? "留空表示不设置" : "{}"}
        className="font-mono text-xs leading-5"
        onChange={(event) => {
          const next = event.target.value;
          if (!next.trim()) {
            const empty = nullable ? null : {};
            setLocal({ text: next, value: empty, error: "" });
            onChange(empty);
            return;
          }
          try {
            const parsed: unknown = JSON.parse(next);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
              throw new Error("需要 JSON 对象");
            setLocal({ text: next, value: parsed, error: "" });
            onChange(parsed as Record<string, unknown>);
          } catch (exception) {
            setLocal({
              text: next,
              value,
              error: `JSON 格式不正确：${exception instanceof Error ? exception.message : String(exception)}`,
            });
          }
        }}
      />
    </Field>
  );
}

/** A list of strings, one per line (command arguments, allowlists). */
export function ListField({
  label,
  hint,
  value,
  onChange,
  disabled,
  placeholder,
}: {
  label: string;
  hint?: React.ReactNode;
  value: string[] | null | undefined;
  onChange: (value: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const items = value ?? [];
  const [local, setLocal] = useState<{ text: string; value: string[] } | null>(
    null,
  );
  const text =
    local && sameValue(local.value, items) ? local.text : items.join("\n");
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <Textarea
        id={id}
        value={text}
        disabled={disabled}
        spellCheck={false}
        placeholder={placeholder}
        rows={Math.min(10, Math.max(2, text.split("\n").length))}
        className="font-mono text-xs leading-5"
        onChange={(event) => {
          const next = event.target.value;
          const parsed = next
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
          setLocal({ text: next, value: parsed });
          onChange(parsed);
        }}
      />
    </Field>
  );
}
