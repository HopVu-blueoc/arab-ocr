import { useCallback, useRef, useState } from "react";
import { createBatch, deleteBatch, exportBatch, getBatches, uploadImages } from "../api/client";
import { anyBatchInFlight } from "../api/activity";
import { pollErrorMessage } from "../api/poll";
import { UPLOAD_CHUNK_SIZE, chunk, imageFilesFrom, summarizeUpload } from "../api/uploads";
import type { BatchDto, UploadFailureDto } from "../api/types";
import { usePagedPoll } from "../hooks/usePagedPoll";

const BATCH_PAGE_SIZE = 50;

const batchKey = (b: BatchDto) => b.id;

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
  onDeleted,
}: {
  activeId: number | null;
  onPick: (batchId: number) => void;
  onDeleted: (batchId: number) => void;
}) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped after a submit so picking the same folder again still fires change.
  const [inputKey, setInputKey] = useState(0);
  const nameTouched = useRef(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const fetchPage = useCallback(
    (cursor: string | null) => getBatches({ limit: BATCH_PAGE_SIZE, cursor }),
    [],
  );

  const {
    items: batches,
    hasMore,
    loadingMore,
    loadMore,
    reload,
    failures,
  } = usePagedPoll<BatchDto>({
    fetchPage,
    itemKey: batchKey,
    hasWork: anyBatchInFlight,
  });

  // A user action resets to one fresh page; only the poll merges.
  const refresh = reload;
  const pollError = pollErrorMessage(failures);

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
        setProgress({ sent, total, fraction: 0 });
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

  function toggleSelected(batchId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(batchId)) next.delete(batchId);
      else next.add(batchId);
      return next;
    });
  }

  async function deleteOne(batch: BatchDto) {
    const confirmed = confirm(
      `Delete batch "${batch.name}" and its ${batch.image_count} image(s)? This cannot be undone.`,
    );
    if (!confirmed) return;
    try {
      await deleteBatch(batch.id);
      if (batch.id === activeId) onDeleted(batch.id);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteSelected() {
    const ids = [...selected];
    if (ids.length === 0) return;
    const names = batches.filter((b) => selected.has(b.id)).map((b) => b.name);
    const confirmed = confirm(
      `Delete ${ids.length} batch(es)? This cannot be undone.\n\n${names.join("\n")}`,
    );
    if (!confirmed) return;

    const results = await Promise.allSettled(ids.map((id) => deleteBatch(id)));
    const failed = results.filter((r) => r.status === "rejected").length;
    setNotice(
      failed > 0
        ? `${ids.length - failed}/${ids.length} batch(es) deleted, ${failed} failed`
        : `${ids.length} batch(es) deleted`,
    );
    if (activeId !== null && selected.has(activeId)) onDeleted(activeId);
    setSelected(new Set());
    setSelectMode(false);
    await refresh();
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
      {/* Kept apart from `notice`, which holds upload summaries the user needs. */}
      {pollError && <p className="batch-notice batch-poll-error">{pollError}</p>}
      <div className="batch-list-toolbar">
        {selectMode ? (
          <>
            <button
              className="batch-delete-selected"
              disabled={selected.size === 0}
              onClick={deleteSelected}
            >
              Delete selected ({selected.size})
            </button>
            <button
              onClick={() => {
                setSelectMode(false);
                setSelected(new Set());
              }}
            >
              Cancel
            </button>
          </>
        ) : (
          batches.length > 0 && <button onClick={() => setSelectMode(true)}>Select</button>
        )}
      </div>
      {batches.map((b) => (
        <div key={b.id} className="batch-entry">
          <div className="batch-row">
            {selectMode && (
              <input
                type="checkbox"
                className="batch-checkbox"
                checked={selected.has(b.id)}
                onChange={() => toggleSelected(b.id)}
                aria-label={`Select batch ${b.name}`}
              />
            )}
            <button
              className={`batch-item${b.id === activeId ? " batch-item-active" : ""}`}
              aria-current={b.id === activeId}
              onClick={() => (selectMode ? toggleSelected(b.id) : onPick(b.id))}
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
          </div>
          <div className="batch-exports">
            <button onClick={() => runExport(b.id, "jsonl")}>JSONL</button>
            <button onClick={() => runExport(b.id, "txt")}>.txt</button>
            {!selectMode && (
              <button
                className="batch-delete"
                title={`Delete batch "${b.name}"`}
                onClick={() => deleteOne(b)}
              >
                🗑
              </button>
            )}
          </div>
        </div>
      ))}
      {hasMore && (
        <button className="batch-load-more" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </aside>
  );
}
