/**
 * Thin API client for the FastAPI backend. The dev server proxies /api → :8000
 * (see vite.config.ts), so all calls below are relative.
 */

export type ContractType =
  | "Public Research Contract"
  | "Commercial Research Contract"
  | "Research Subcontract"
  | "Material Transfer Agreement"
  | "Data Transfer Agreement"
  | "Data Access Agreement"
  | "Collaboration Agreement"
  | "Confidential Disclosure Agreement"
  | "Master Services Agreement"
  | "Provision of Services Agreement"
  | "Consultancy Services Agreement"
  | "Clinical Trial Research Agreement"
  | "Student Research Agreement"
  | "Unknown";

export type FlagLevel = "green" | "amber" | "red" | "blue";

export interface FlagItem {
  level: FlagLevel;
  clause_id: string;
  clause_title: string;
  snippet: string;
  rationale: string;
  standard_ref: string | null;
}

export interface ReviewMetrics {
  n_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
  total_cost_usd: number;
  backend: string;
  model: string;
}

export interface ContractReview {
  document_id: string;
  filename: string;
  contract_type: ContractType;
  contract_type_confidence: number;
  summary: string;
  flags: FlagItem[];
  counts: Record<FlagLevel, number>;
  generated_at: string;
  metrics: ReviewMetrics;
  references_used: string[];
  clause_count: number | null;
  compare_counts: Record<FlagLevel, number> | null;
  clauses_list: Array<{ id: string; title: string }> | null;
  compare_flags: FlagItem[] | null;
}

export interface UoaPosition {
  id: string;
  topic: string;
  category: string;
  preferred: string;
  acceptable: string;
  escalation_to: string;
  applies_to: string[];
}

export interface ClauseItem {
  id: string;
  title: string;
  text: string;
}

export interface ClauseListResponse {
  document_id: string;
  clause_count: number;
  clauses: ClauseItem[];
}

export interface CompareResult {
  flags: FlagItem[];
  counts: Record<FlagLevel, number>;
}

export interface SampleEntry {
  id: string;
  label: string;
  description: string;
  filename: string;
  contract_type_hint: string;
  size_bytes: number;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  char_count: number;
  clause_count: number;
}

export interface ClassifyResponse {
  document_id: string;
  filename: string;
  contract_type: ContractType;
  confidence: number;
  rationale: string;
}

export interface HealthResponse {
  status: "ok";
  llm_configured: boolean;
  llm_status: string;
  supported_uploads: string[];
  max_upload_mb: number;
}

const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function jfetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => jfetch<HealthResponse>("/health"),

  positions: () =>
    jfetch<{ source: string; positions: UoaPosition[] }>("/positions"),

  upload: async (file: File): Promise<UploadResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    return jfetch<UploadResponse>("/upload", { method: "POST", body: fd });
  },

  classify: (documentId: string) =>
    jfetch<ClassifyResponse>(`/classify/${documentId}`, { method: "POST" }),

  review: (documentId: string) =>
    jfetch<ContractReview>(`/review/${documentId}`, { method: "POST" }),

  samples: () => jfetch<{ samples: SampleEntry[] }>("/samples"),

  clauses: (documentId: string) =>
    jfetch<ClauseListResponse>(`/document/${documentId}/clauses`),

  compare: (documentId: string) =>
    jfetch<CompareResult>(`/compare/${documentId}`, { method: "POST" }),

  augment: (documentId: string) =>
    jfetch<CompareResult>(`/augment/${documentId}`, { method: "POST" }),

  summarize: (documentId: string) =>
    jfetch<ContractReview>(`/summary/${documentId}`, { method: "POST" }),

  loadSample: (sampleId: string) =>
    jfetch<UploadResponse>(`/samples/${sampleId}/load`, { method: "POST" }),

  cachedReport: (sampleId: string) =>
    jfetch<ContractReview>(`/samples/${sampleId}/report`),

  chat: (body: {
    document_id?: string;
    history: { role: "user" | "assistant"; content: string }[];
    message: string;
  }) =>
    jfetch<{ reply: string; citations: string[] }>("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
