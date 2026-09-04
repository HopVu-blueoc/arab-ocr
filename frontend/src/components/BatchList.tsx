import { useEffect, useState } from "react";
import { createBatch, getBatches } from "../api/client";
import type { BatchDto } from "../api/types";

export function BatchList({ onPick }: { onPick: (batchId: number) => void }) {
  const [batches, setBatches] = useState<BatchDto[]>([]);
  const [name, setName] = useState("");
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = () => getBatches().then(setBatches).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // progress ticks while OCR runs
    return () => clearInterval(timer);
  }, []);

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

  return (
    <aside className="batch-list">
      <form onSubmit={submit} className="batch-form">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Batch name"
          required
        />
        <input
          value={dir}
          onChange={(e) => setDir(e.target.value)}
          placeholder="/path/to/images"
          required
        />
        <button disabled={busy}>{busy ? "Importing…" : "Import folder"}</button>
      </form>
      {batches.map((b) => (
        <button key={b.id} className="batch-item" onClick={() => onPick(b.id)}>
          <span>{b.name}</span>
          <span className="muted">
            {b.done_count + b.approved_count}/{b.image_count} done
            {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
          </span>
        </button>
      ))}
    </aside>
  );
}
