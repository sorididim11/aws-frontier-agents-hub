import { readFileSync, existsSync } from "fs";
import { join } from "path";

export interface Chunk {
  id: string;
  source: string;
  section: string;
  content: string;
  embedding: number[];
}

let chunks: Chunk[] = [];

const DATA_PATH = join(
  new URL(".", import.meta.url).pathname,
  "../../data/embeddings.json"
);

export function loadStore(customPath?: string): void {
  const path = customPath || DATA_PATH;
  if (!existsSync(path)) {
    console.warn(`[RAG] embeddings.json not found at ${path}. Run 'npm run index-docs' to create.`);
    return;
  }
  try {
    const raw = readFileSync(path, "utf-8");
    chunks = JSON.parse(raw) as Chunk[];
    console.log(`[RAG] Loaded ${chunks.length} chunks from ${path}`);
  } catch (e) {
    console.error("[RAG] Failed to load embeddings:", e);
  }
}

function cosineSimilarity(a: number[], b: number[]): number {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB) || 1);
}

export interface SearchResult {
  source: string;
  section: string;
  content: string;
  score: number;
}

export function search(queryEmbedding: number[], topK: number = 5): SearchResult[] {
  if (chunks.length === 0) return [];

  const scored = chunks.map((chunk) => ({
    source: chunk.source,
    section: chunk.section,
    content: chunk.content,
    score: cosineSimilarity(queryEmbedding, chunk.embedding),
  }));

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, topK);
}

export function isLoaded(): boolean {
  return chunks.length > 0;
}
