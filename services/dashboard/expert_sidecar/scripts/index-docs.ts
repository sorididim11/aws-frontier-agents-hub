#!/usr/bin/env npx tsx
/**
 * RAG Document Indexer
 * Usage: npm run index-docs
 *
 * Reads project documentation, chunks it, generates Bedrock Titan embeddings,
 * and saves to data/embeddings.json for runtime semantic search.
 */
import { join } from "path";
import { indexDocuments } from "../src/rag/indexer.js";

const __dirname = new URL(".", import.meta.url).pathname;
const PROJECT_ROOT = join(__dirname, "../../../..");
const OUTPUT_PATH = join(__dirname, "../data/embeddings.json");

async function main() {
  console.log("=== RAG Document Indexer ===");
  console.log(`Project root: ${PROJECT_ROOT}`);
  console.log(`Output: ${OUTPUT_PATH}`);
  console.log("");

  const start = Date.now();
  const count = await indexDocuments(PROJECT_ROOT, OUTPUT_PATH);
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);

  console.log("");
  console.log(`Done! ${count} chunks indexed in ${elapsed}s`);
}

main().catch((e) => {
  console.error("Indexing failed:", e);
  process.exit(1);
});
