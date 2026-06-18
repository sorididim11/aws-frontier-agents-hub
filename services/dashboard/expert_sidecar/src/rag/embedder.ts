import { pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

const MODEL_ID = "Xenova/all-MiniLM-L6-v2";
const DIMENSION = 384;

let extractor: FeatureExtractionPipeline | null = null;

async function getExtractor(): Promise<FeatureExtractionPipeline> {
  if (!extractor) {
    console.log("[RAG] Loading embedding model (first time may download ~23MB)...");
    extractor = await pipeline("feature-extraction", MODEL_ID, {
      dtype: "fp32",
    });
    console.log("[RAG] Embedding model loaded.");
  }
  return extractor;
}

export async function embed(text: string): Promise<number[]> {
  const ext = await getExtractor();
  const output = await ext(text.slice(0, 512), { pooling: "mean", normalize: true });
  return Array.from(output.data as Float32Array);
}

export async function embedBatch(texts: string[]): Promise<number[][]> {
  const ext = await getExtractor();
  const results: number[][] = [];
  // Process in batches of 16
  for (let i = 0; i < texts.length; i += 16) {
    const batch = texts.slice(i, i + 16).map((t) => t.slice(0, 512));
    for (const text of batch) {
      const output = await ext(text, { pooling: "mean", normalize: true });
      results.push(Array.from(output.data as Float32Array));
    }
    if (i % 64 === 0 && i > 0) {
      console.log(`[Indexer] Embedded ${i}/${texts.length} chunks...`);
    }
  }
  return results;
}

export { DIMENSION };
