import { readFileSync, readdirSync, statSync, writeFileSync } from "fs";
import { join, relative, extname } from "path";
import { embed, embedBatch } from "./embedder.js";
import type { Chunk } from "./store.js";

const CHUNK_SIZE = 1500; // characters (~500 tokens)
const OVERLAP = 200;

interface DocSource {
  path: string;
  glob?: string;
}

export function getDefaultSources(projectRoot: string): DocSource[] {
  return [
    { path: join(projectRoot, ".claude/skills"), glob: "SKILL.md" },
    { path: join(projectRoot, "docs"), glob: "*.md" },
    { path: join(projectRoot, "services/dashboard/prompts"), glob: "*.md" },
    { path: join(projectRoot, "simulator/engine"), glob: "failure_modes.py" },
    { path: join(projectRoot, "services/dashboard/scenarios") },
  ];
}

function findFiles(dir: string, glob?: string): string[] {
  const results: string[] = [];
  try {
    const entries = readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = join(dir, entry.name);
      if (entry.isDirectory()) {
        results.push(...findFiles(fullPath, glob));
      } else if (entry.isFile()) {
        if (glob) {
          const ext = extname(entry.name);
          const pattern = glob.replace("*", "");
          if (entry.name === glob || entry.name.endsWith(pattern)) {
            results.push(fullPath);
          }
        } else {
          const ext = extname(entry.name);
          if ([".md", ".py", ".json", ".yaml", ".yml"].includes(ext)) {
            results.push(fullPath);
          }
        }
      }
    }
  } catch {
    // directory doesn't exist
  }
  return results;
}

function chunkText(text: string, source: string): { section: string; content: string }[] {
  const ext = extname(source);

  if (ext === ".json") {
    // JSON: treat each top-level item as a chunk
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        return parsed.map((item, i) => ({
          section: `item_${i}`,
          content: JSON.stringify(item, null, 2).slice(0, CHUNK_SIZE),
        }));
      }
      return [{ section: "root", content: text.slice(0, CHUNK_SIZE * 3) }];
    } catch {
      return [{ section: "raw", content: text.slice(0, CHUNK_SIZE) }];
    }
  }

  // Markdown/Python: split by headers or large blocks
  const sections: { section: string; content: string }[] = [];
  const lines = text.split("\n");
  let currentSection = "top";
  let buffer = "";

  for (const line of lines) {
    const headerMatch = line.match(/^#{1,3}\s+(.+)/);
    if (headerMatch && buffer.length > 100) {
      pushChunks(sections, currentSection, buffer);
      currentSection = headerMatch[1].trim();
      buffer = "";
    }
    buffer += line + "\n";

    if (buffer.length > CHUNK_SIZE) {
      pushChunks(sections, currentSection, buffer);
      buffer = buffer.slice(-OVERLAP);
    }
  }
  if (buffer.trim()) {
    pushChunks(sections, currentSection, buffer);
  }

  return sections;
}

function pushChunks(
  arr: { section: string; content: string }[],
  section: string,
  text: string
): void {
  if (text.trim().length < 50) return;
  arr.push({ section, content: text.trim() });
}

export async function indexDocuments(
  projectRoot: string,
  outputPath: string,
  sources?: DocSource[]
): Promise<number> {
  const docSources = sources || getDefaultSources(projectRoot);
  const allFiles: string[] = [];

  for (const src of docSources) {
    allFiles.push(...findFiles(src.path, src.glob));
  }

  console.log(`[Indexer] Found ${allFiles.length} files to index`);

  const allChunks: Omit<Chunk, "embedding">[] = [];

  for (const file of allFiles) {
    const content = readFileSync(file, "utf-8");
    const relPath = relative(projectRoot, file);
    const chunks = chunkText(content, file);

    for (let i = 0; i < chunks.length; i++) {
      allChunks.push({
        id: `${relPath}#${i}`,
        source: relPath,
        section: chunks[i].section,
        content: chunks[i].content,
      });
    }
  }

  console.log(`[Indexer] ${allChunks.length} chunks to embed`);

  const texts = allChunks.map((c) => `${c.source} | ${c.section}\n${c.content}`);
  const embeddings = await embedBatch(texts);

  const finalChunks: Chunk[] = allChunks.map((c, i) => ({
    ...c,
    embedding: embeddings[i],
  }));

  writeFileSync(outputPath, JSON.stringify(finalChunks));
  console.log(`[Indexer] Wrote ${finalChunks.length} chunks to ${outputPath}`);

  return finalChunks.length;
}
