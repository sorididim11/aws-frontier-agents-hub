import { embed } from "./embedder.js";
import { loadStore, search, isLoaded } from "./store.js";
import type { SearchResult } from "./store.js";

export { loadStore, isLoaded };

export async function ragSearch(
  query: string,
  topK: number = 5
): Promise<SearchResult[]> {
  if (!isLoaded()) {
    return [
      {
        source: "system",
        section: "error",
        content: "RAG 인덱스가 로드되지 않았습니다. 'npm run index-docs'를 실행해주세요.",
        score: 0,
      },
    ];
  }

  const queryEmbedding = await embed(query);
  return search(queryEmbedding, topK);
}
