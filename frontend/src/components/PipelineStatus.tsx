import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ChevronDown, Circle, FileSearch, FileUp, GitCompare, Loader2, ScrollText, Sparkles } from "lucide-react";
import { useState } from "react";
import type { ClassifyResponse, ClauseItem, FlagItem, UoaPosition } from "../lib/api";

type Phase =
  | "idle"
  | "uploading"
  | "classifying"
  | "comparing"
  | "augmenting"
  | "summarizing"
  | "done"
  | "error";

interface Props {
  phase: Phase;
  filename?: string | null;
  clauseCount?: number | null;
  clauses?: ClauseItem[] | null;
  classification?: ClassifyResponse | null;
  compareCounts?: Record<string, number> | null;
  compareFlags?: FlagItem[] | null;
  augmentCounts?: Record<string, number> | null;
  augmentFlags?: FlagItem[] | null;
  summaryText?: string | null;
  positions?: UoaPosition[];
}

interface StepDef {
  phase: Phase;
  label: string;
  icon: React.ElementType;
  subtasks: string[];
  detail: (props: Props) => string | null;
  renderDetails?: (props: Props) => React.ReactNode;
}

const FLAG_COLORS: Record<string, string> = {
  red: "text-flag-red bg-flag-red/10",
  amber: "text-amber-600 bg-amber-50",
  green: "text-emerald-600 bg-emerald-50",
  blue: "text-blue-500 bg-blue-50",
};
const FLAG_EMOJI: Record<string, string> = { red: "🔴", amber: "🟡", green: "🟢", blue: "🔵" };

const STEPS: StepDef[] = [
  {
    phase: "uploading",
    label: "Ingest",
    icon: FileUp,
    subtasks: ["PDF text extraction", "LLM-assisted clause boundary detection", "Build clause list"],
    detail: ({ clauseCount }) =>
      clauseCount != null ? `${clauseCount} clauses extracted` : "Parsing PDF and splitting clauses",
    renderDetails: ({ clauses }) => clauses && clauses.length > 0 ? (
      <ClauseList clauses={clauses} />
    ) : null,
  },
  {
    phase: "classifying",
    label: "Classify",
    icon: FileSearch,
    subtasks: ["Keyword baseline matching", "GPT-4o contract type classification", "Output type and confidence"],
    detail: ({ classification }) =>
      classification
        ? `${classification.contract_type} · ${Math.round(classification.confidence * 100)}%`
        : "Identifying contract type",
    renderDetails: ({ classification }) => classification ? (
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[12px]">
          <span className="text-ink-500 w-20 shrink-0">Type</span>
          <span className="font-medium text-ink-900">{classification.contract_type}</span>
        </div>
        <div className="flex items-center gap-2 text-[12px]">
          <span className="text-ink-500 w-20 shrink-0">Confidence</span>
          <span className="font-medium text-ink-900">{Math.round(classification.confidence * 100)}%</span>
        </div>
        {classification.rationale && (
          <div className="flex gap-2 text-[12px]">
            <span className="text-ink-500 w-20 shrink-0">Rationale</span>
            <span className="text-ink-700">{classification.rationale}</span>
          </div>
        )}
      </div>
    ) : null,
  },
  {
    phase: "comparing",
    label: "Compare",
    icon: GitCompare,
    subtasks: ["Clause topic detection (keyword scoring)", "Match against UoA Contracting Positions", "Emit seed flags (no LLM)"],
    detail: ({ compareCounts }) =>
      compareCounts
        ? `🔴 ${compareCounts.red ?? 0}  🟡 ${compareCounts.amber ?? 0}  🟢 ${compareCounts.green ?? 0}  🔵 ${compareCounts.blue ?? 0}`
        : "Deterministic rule-based flagging",
    renderDetails: ({ compareFlags, positions }) => compareFlags && compareFlags.length > 0 ? (
      <FlagList flags={compareFlags} positions={positions ?? []} />
    ) : null,
  },
  {
    phase: "augmenting",
    label: "Augment",
    icon: Sparkles,
    subtasks: ["Send each clause to GPT-4o", "Re-grade amber flags", "Generate rationale and standard refs"],
    detail: ({ augmentCounts }) =>
      augmentCounts
        ? `🔴 ${augmentCounts.red ?? 0}  🟡 ${augmentCounts.amber ?? 0}  🟢 ${augmentCounts.green ?? 0}  🔵 ${augmentCounts.blue ?? 0}`
        : "LLM per-clause re-grading",
    renderDetails: ({ augmentFlags, positions }) => augmentFlags && augmentFlags.length > 0 ? (
      <FlagList flags={augmentFlags} positions={positions ?? []} />
    ) : null,
  },
  {
    phase: "summarizing",
    label: "Summary",
    icon: ScrollText,
    subtasks: ["Generate executive summary", "Aggregate flag counts", "Build final review report"],
    detail: () => "Report ready",
    renderDetails: ({ summaryText }) => summaryText ? (
      <p className="text-[12px] text-ink-700 leading-relaxed">{summaryText}</p>
    ) : null,
  },
];

const PHASE_INDEX: Record<Phase, number> = {
  idle: -1,
  uploading: 0,
  classifying: 1,
  comparing: 2,
  augmenting: 3,
  summarizing: 4,
  done: 4,
  error: -1,
};

const PROGRESS: Record<Phase, number> = {
  idle: 0,
  uploading: 10,
  classifying: 30,
  comparing: 52,
  augmenting: 74,
  summarizing: 92,
  done: 100,
  error: 100,
};

export function PipelineStatus({ phase, filename, clauseCount, clauses, classification, compareCounts, compareFlags, augmentCounts, augmentFlags, summaryText, positions }: Props) {
  const [expanded, setExpanded] = useState(phase !== "done");
  const [openStepIndex, setOpenStepIndex] = useState<number | null>(null);
  const isDone = phase === "done";
  const currentIndex = PHASE_INDEX[phase];
  const props: Props = { phase, filename, clauseCount, clauses, classification, compareCounts, compareFlags, augmentCounts, augmentFlags, summaryText, positions };

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.28 }}
      className="glass rounded-xl p-4 sm:p-5"
    >
      {/* Header */}
      <div
        className={`flex items-center justify-between gap-4 ${isDone ? "cursor-pointer select-none" : ""}`}
        onClick={() => isDone && setExpanded((v) => !v)}
      >
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-ink-500">Pipeline</div>
          <div className="mt-0.5 truncate text-[14px] font-medium text-ink-900">
            {headerText(phase, filename)}
          </div>
        </div>
        {isDone ? (
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-[11px] text-ink-400">{expanded ? "Collapse" : "Expand"}</span>
            <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: 0.2 }}>
              <ChevronDown className="size-4 text-ink-400" />
            </motion.div>
          </div>
        ) : (
          <Loader2 className="size-5 shrink-0 animate-spin text-ink-400" />
        )}
      </div>

      {/* Progress bar */}
      <div className="mt-3 h-1 overflow-hidden rounded-full bg-ink-100">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${PROGRESS[phase]}%` }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="h-full rounded-full bg-ink-800"
        />
      </div>

      {/* Steps */}
      <AnimatePresence initial={false}>
        {expanded && (
      <motion.div
        initial={{ height: 0, opacity: 0 }}
        animate={{ height: "auto", opacity: 1 }}
        exit={{ height: 0, opacity: 0 }}
        transition={{ duration: 0.25, ease: "easeInOut" }}
        className="overflow-hidden"
      >
      <div className="mt-4 space-y-1.5">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          const complete = index < currentIndex || phase === "done";
          const active = index === currentIndex && phase !== "done" && phase !== "error";
          const pending = !complete && !active;
          const detailText = step.detail(props);
          const detailContent = step.renderDetails?.(props);
          const isStepOpen = openStepIndex === index;
          const canExpand = complete && !!detailContent;

          return (
            <div
              key={step.phase}
              className={`rounded-lg ring-1 transition-colors duration-200 ${
                complete
                  ? "bg-white ring-ink-200"
                  : active
                    ? "bg-ink-50 ring-ink-200"
                    : "bg-white/60 ring-ink-100"
              }`}
            >
              {/* Row */}
              <div
                className={`flex items-center gap-2.5 px-3 py-2.5 ${canExpand ? "cursor-pointer" : ""}`}
                onClick={() => canExpand && setOpenStepIndex(isStepOpen ? null : index)}
              >
                <div className={`flex size-5 shrink-0 items-center justify-center ${
                  complete ? "text-ink-600" : active ? "text-ink-800" : "text-ink-300"
                }`}>
                  {complete ? <CheckCircle2 className="size-4" /> : active ? <Icon className="size-4" /> : <Circle className="size-4" />}
                </div>

                <span className={`text-[13px] font-medium ${
                  complete ? "text-ink-900" : active ? "text-ink-900" : "text-ink-400"
                }`}>
                  {step.label}
                </span>

                {complete && detailText && (
                  <span className="ml-auto truncate text-[11px] text-ink-400 mr-1">{detailText}</span>
                )}
                {complete && canExpand && (
                  <motion.div animate={{ rotate: isStepOpen ? 180 : 0 }} transition={{ duration: 0.18 }}>
                    <ChevronDown className="size-3.5 text-ink-400 shrink-0" />
                  </motion.div>
                )}
                {active && (
                  <span className="ml-auto flex items-center gap-1 text-[11px] text-ink-500">
                    <span className="inline-flex gap-0.5">
                      {[0, 1, 2].map((i) => (
                        <motion.span
                          key={i}
                          className="block size-1 rounded-full bg-ink-400"
                          animate={{ opacity: [0.3, 1, 0.3] }}
                          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
                        />
                      ))}
                    </span>
                    Running
                  </span>
                )}
                {pending && <span className="ml-auto text-[11px] text-ink-300">Waiting</span>}
              </div>

              {/* Subtasks — active step */}
              <AnimatePresence initial={false}>
                {active && (
                  <motion.ul
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.22, ease: "easeInOut" }}
                    className="overflow-hidden border-t border-ink-100 px-3 pb-2.5 pt-2 space-y-1.5"
                  >
                    {step.subtasks.map((task, ti) => (
                      <motion.li
                        key={task}
                        initial={{ opacity: 0, x: -4 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: ti * 0.06 }}
                        className="flex items-start gap-2 text-[12px] text-ink-600"
                      >
                        <motion.span
                          className="mt-[4px] size-1.5 shrink-0 rounded-full bg-ink-300"
                          animate={{ backgroundColor: ["#cbd5e1", "#475569", "#cbd5e1"] }}
                          transition={{ duration: 2, repeat: Infinity, delay: ti * 0.4 }}
                        />
                        {task}
                      </motion.li>
                    ))}
                  </motion.ul>
                )}
              </AnimatePresence>

              {/* Expandable details — completed step */}
              <AnimatePresence initial={false}>
                {isStepOpen && detailContent && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.22, ease: "easeInOut" }}
                    className="overflow-hidden"
                  >
                    <div className="border-t border-ink-100 bg-ink-50 px-3 pb-3 pt-2.5 max-h-72 overflow-y-auto rounded-b-lg">
                      {detailContent}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
      </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function ClauseList({ clauses }: { clauses: ClauseItem[] }) {
  return (
    <ul className="space-y-0.5">
      {clauses.map((c, i) => {
        const displayId = c.id.split("__dup__")[0];
        const displayTitle = c.title.replace(/\*\*/g, "").trim();
        return (
          <li key={c.id} className="flex items-baseline gap-2 text-[11px] min-w-0">
            <span className="shrink-0 w-5 text-right text-ink-400">{i + 1}.</span>
            <span className="text-ink-700 break-words min-w-0">{displayTitle || <em className="text-ink-300">—</em>}</span>
          </li>
        );
      })}
    </ul>
  );
}

function FlagList({ flags, positions }: { flags: FlagItem[]; positions: UoaPosition[] }) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const posMap = Object.fromEntries(positions.map((p) => [p.id, p]));
  const order = ["red", "amber", "green", "blue"];
  const sorted = [...flags].sort((a, b) => order.indexOf(a.level) - order.indexOf(b.level));

  return (
    <ul className="space-y-2.5">
      {sorted.map((f, i) => {
        const displayTitle = f.clause_title.replace(/\*\*/g, "").trim();
        const posId = f.standard_ref?.match(/POS-\w+/)?.[0];
        const pos = posId ? posMap[posId] : null;
        const isOpen = openIdx === i;

        return (
          <li key={i} className="text-[11px] rounded-lg ring-1 ring-ink-100 overflow-hidden">
            <div
              className={`flex items-start gap-1.5 px-2.5 py-2 min-w-0 ${pos ? "cursor-pointer" : ""}`}
              onClick={() => pos && setOpenIdx(isOpen ? null : i)}
            >
              <span className="shrink-0 mt-px">{FLAG_EMOJI[f.level]}</span>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink-800 break-words">{displayTitle}</div>
                {f.standard_ref && (
                  <div className="mt-0.5 text-ink-400">{f.standard_ref}</div>
                )}
                {f.rationale && (
                  <div className="mt-0.5 text-ink-500">{f.rationale}</div>
                )}
              </div>
              {pos && (
                <motion.div animate={{ rotate: isOpen ? 180 : 0 }} transition={{ duration: 0.18 }} className="shrink-0 mt-0.5">
                  <ChevronDown className="size-3 text-ink-400" />
                </motion.div>
              )}
            </div>

            <AnimatePresence initial={false}>
              {isOpen && pos && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2, ease: "easeInOut" }}
                  className="overflow-hidden"
                >
                  <div className="border-t border-ink-100 bg-white px-2.5 py-2 space-y-1.5">
                    <div className="font-semibold text-ink-700">{pos.topic}</div>
                    <div>
                      <span className="text-emerald-600 font-medium">Preferred: </span>
                      <span className="text-ink-600">{pos.preferred}</span>
                    </div>
                    {pos.acceptable && (
                      <div>
                        <span className="text-amber-600 font-medium">Acceptable: </span>
                        <span className="text-ink-600">{pos.acceptable}</span>
                      </div>
                    )}
                    {pos.escalation_to && (
                      <div>
                        <span className="text-flag-red font-medium">Escalation: </span>
                        <span className="text-ink-600">{pos.escalation_to}</span>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </li>
        );
      })}
    </ul>
  );
}

function headerText(phase: Phase, filename?: string | null) {
  const name = filename ?? "document";
  if (phase === "uploading") return `Parsing ${name}`;
  if (phase === "classifying") return "Identifying contract type…";
  if (phase === "comparing") return "Running rule-based comparison…";
  if (phase === "augmenting") return "LLM per-clause review…";
  if (phase === "summarizing") return "Generating report…";
  if (phase === "done") return "Review complete";
  if (phase === "error") return "Processing stopped";
  return "Ready";
}
