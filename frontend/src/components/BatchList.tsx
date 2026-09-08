import { useEffect, useRef, useState } from "react";
import { createBatch, exportBatch, getBatches, uploadImages } from "../api/client";
import { UPLOAD_CHUNK_SIZE, chunk, imageFilesFrom, summarizeUpload } from "../api/uploads";
import type { BatchDto, UploadFailureDto } from "../api/types";

// React's InputHTMLAttributes has no webkitdirectory, but it is a real
// attribute Chromium and WebKit honour, and the browser walks the tree so the
// server never has to.
const DIRECTORY_ATTRS = {
  webkitdirectory: "",
  directory: "",
} as unknown as React.InputHTMLAttributes<HTMLInputElement>;

type Progress = { sent: number; total: number; fraction: number };

export function BatchList({
  activeId,
  onPick,
}: {
  activeId: number | null;
  onPick: (batchId: number) => void;
}) {
  const [batches, setBatches] = useState<BatchDto[]>([]);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped after a submit so picking the same folder again still fires change.
  const [inputKey, setInputKey] = useState(0);
  const nameTouched = useRef(false);

  const refresh = () => getBatches().then(setBatches).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // progress ticks while OCR runs
    return () => clearInterval(timer);
  }, []);

  function choose(list: FileList | null) {
    const picked = imageFilesFrom(list);
    setFiles(picked);
    setNotice(
      list && picked.length < list.length
        ? `${list.length - picked.length} non-image file(s) ignored`
        : null,
    );
    if (!nameTouched.current && picked.length > 0) {
      // webkitRelativePath is "folder/sub/file.png" for a directory pick.
      const folder = picked[0].webkitRelativePath?.split("/")[0];
      setName(folder || picked[0].name.replace(/\.[^.]+$/, ""));
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setNotice(null);
    const total = files.length;
    let imported = 0;
    let skipped = 0;
    const failed: UploadFailureDto[] = [];
    let sent = 0;
    let batchId: number | null = null;

    try {
      const batch = await createBatch(name.trim());
      batchId = batch.id;
      for (const group of chunk(files, UPLOAD_CHUNK_SIZE)) {
        const result = await uploadImages(batch.id, group, (fraction) =>
          setProgress({ sent, total, fraction }),
        );
        imported += result.imported;
        skipped += result.skipped;
        failed.push(...result.failed);
        sent += group.length;
        setProgress({ sent, total, fraction: 1 });
      }
      setNotice(summarizeUpload({ imported, skipped, failed }));
      await refresh();
      onPick(batch.id);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      setNotice(
        batchId === null
          ? errorMessage
          : `${errorMessage} — some files may have already been uploaded to this batch; check the sidebar before retrying.`,
      );
    } finally {
      setFiles([]);
      setName("");
      nameTouched.current = false;
      setInputKey((k) => k + 1);
      setProgress(null);
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

  const uploading = progress !== null;
  const barFraction = progress
    ? (progress.sent +
        progress.fraction * Math.min(UPLOAD_CHUNK_SIZE, progress.total - progress.sent)) /
      Math.max(progress.total, 1)
    : 0;

  return (
    <aside className="batch-list">
      <form onSubmit={submit} className="batch-form">
        <label className="file-pick">
          <span>🖼 Choose images</span>
          <input
            key={`files-${inputKey}`}
            type="file"
            multiple
            accept="image/*"
            disabled={uploading}
            onChange={(e) => choose(e.target.files)}
          />
        </label>
        <label className="file-pick">
          <span>📁 Choose a folder</span>
          <input
            key={`dir-${inputKey}`}
            type="file"
            multiple
            disabled={uploading}
            onChange={(e) => choose(e.target.files)}
            {...DIRECTORY_ATTRS}
          />
        </label>
        {files.length > 0 && <p className="muted">{files.length} image(s) ready</p>}
        <input
          value={name}
          onChange={(e) => {
            nameTouched.current = true;
            setName(e.target.value);
          }}
          placeholder="Batch name"
          required
        />
        <button disabled={uploading || files.length === 0 || !name.trim()}>
          {uploading ? "Uploading…" : "Upload"}
        </button>
        {progress && (
          <div className="upload-progress">
            <div className="batch-bar">
              <span style={{ width: `${barFraction * 100}%` }} />
            </div>
            <p className="muted">
              {progress.sent}/{progress.total} uploaded
            </p>
          </div>
        )}
      </form>
      {notice && <p className="batch-notice">{notice}</p>}
      {batches.map((b) => (
        <div key={b.id} className="batch-entry">
          <button
            className={`batch-item${b.id === activeId ? " batch-item-active" : ""}`}
            aria-current={b.id === activeId}
            onClick={() => onPick(b.id)}
          >
            <span>{b.name}</span>
            <span className="muted">
              {b.done_count + b.approved_count}/{b.image_count} done
              {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
            </span>
            <div className="batch-bar">
              <span
                style={{
                  width: `${
                    b.image_count === 0
                      ? 0
                      : ((b.done_count + b.approved_count) / b.image_count) * 100
                  }%`,
                }}
              />
            </div>
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
