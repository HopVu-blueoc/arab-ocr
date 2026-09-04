import { useEffect, useState } from "react";
import { createBatch, exportBatch, getBatches, pickPath } from "../api/client";
import type { BatchDto } from "../api/types";

export function BatchList({ onPick }: { onPick: (batchId: number) => void }) {
  const [batches, setBatches] = useState<BatchDto[]>([]);
  const [name, setName] = useState("");
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = () => getBatches().then(setBatches).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // progress ticks while OCR runs
    return () => clearInterval(timer);
  }, []);

  async function browse(kind: "folder" | "file") {
    setPicking(true);
    setNotice(null);
    try {
      const { path } = await pickPath(kind);
      if (path === null) return; // dialog cancelled
      setDir(path);
      if (!name.trim()) setName(path.split("/").filter(Boolean).pop() ?? "");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setPicking(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const batch = await createBatch(name, dir);
      setName("");
      setDir("");
      await refresh();
      onPick(batch.id);
    } finally {
      setBusy(false);
    }
  }

  async function runExport(batchId: number, format: "jsonl" | "txt") {
    try {
      const res = await exportBatch(batchId, format);
      setNotice(`Wrote ${res.path}`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <aside className="batch-list">
      <form onSubmit={submit} className="batch-form">
        <div className="browse-row">
          <button type="button" disabled={picking} onClick={() => browse("folder")}>
            📁 Folder…
          </button>
          <button type="button" disabled={picking} onClick={() => browse("file")}>
            🖼 Image…
          </button>
        </div>
        <input
          value={dir}
          onChange={(e) => setDir(e.target.value)}
          placeholder="/path/to/images"
          required
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Batch name"
          required
        />
        <button disabled={busy || !dir || !name}>
          {busy ? "Importing…" : "Import"}
        </button>
      </form>
      {notice && <p className="batch-notice">{notice}</p>}
      {batches.map((b) => (
        <div key={b.id} className="batch-entry">
          <button className="batch-item" onClick={() => onPick(b.id)}>
            <span>{b.name}</span>
            <span className="muted">
              {b.done_count + b.approved_count}/{b.image_count} done
              {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
            </span>
          </button>
          <div className="batch-exports">
            <button onClick={() => runExport(b.id, "jsonl")}>JSONL</button>
            <button onClick={() => runExport(b.id, "txt")}>.txt</button>
          </div>
        </div>
      ))}
    </aside>
  );
}
