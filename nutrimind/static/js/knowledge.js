/* Knowledge base: drag-drop upload, document table, re-index/delete,
   retrieval preview. */
"use strict";

(function () {
  const drop = document.getElementById("kb-drop");
  const fileInput = document.getElementById("kb-file");
  const uploadState = document.getElementById("kb-upload-state");
  const rows = document.getElementById("kb-rows");

  const STATUS_BADGE = {
    indexed: ["nm-badge-green", "bi-check-circle", "indexed"],
    processing: ["nm-badge-amber", "bi-arrow-repeat", "processing"],
    pending: ["nm-badge-amber", "bi-hourglass-split", "pending"],
    failed: ["nm-badge-red", "bi-x-circle", "failed"],
  };

  async function refresh() {
    try {
      const { documents } = await NM.api("/api/documents");
      if (!documents.length) {
        rows.innerHTML = `<tr><td colspan="6">
          <div class="nm-empty my-3"><i class="bi bi-journal-plus"></i>
          No documents yet — upload WHO/ICMR guidelines or any nutrition PDF to
          enable grounded, citable answers.</div></td></tr>`;
        return;
      }
      rows.innerHTML = documents.map((d) => {
        /* Indexed under a previous embedding provider: the row is healthy but
           the vectors live in another collection, so it cannot be searched
           until re-indexed. Saying 'indexed' here is what made retrieval look
           broken rather than stale. */
        const [cls, icon, label] = d.searchable === false
          ? ["nm-badge-amber", "bi-arrow-repeat", "needs re-index"]
          : (STATUS_BADGE[d.status] || STATUS_BADGE.pending);
        return `<tr>
          <td class="text-truncate" style="max-width:220px" title="${NM.esc(d.filename)}">
            <i class="bi bi-file-earmark-pdf me-1 text-2"></i>${NM.esc(d.filename)}
            ${d.error ? `<div class="text-2" style="font-size:.75rem">${NM.esc(d.error)}</div>` : ""}
          </td>
          <td><span class="nm-badge ${cls}"><i class="bi ${icon}"></i> ${label}</span></td>
          <td>${d.pages || "—"}</td>
          <td>${d.chunk_count || "—"}</td>
          <td class="text-2" style="font-size:.83rem">${NM.timeAgo(d.uploaded_at)}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-secondary" data-reindex="${d.id}"
                    title="Re-index" aria-label="Re-index ${NM.esc(d.filename)}">
              <i class="bi bi-arrow-repeat"></i></button>
            <button class="btn btn-sm btn-outline-danger" data-delete="${d.id}"
                    title="Delete" aria-label="Delete ${NM.esc(d.filename)}">
              <i class="bi bi-trash"></i></button>
          </td></tr>`;
      }).join("");
      if (documents.some((d) => ["processing", "pending"].includes(d.status)))
        setTimeout(refresh, 2500);
    } catch (error) {
      rows.innerHTML = `<tr><td colspan="6" class="text-2 p-3">${NM.esc(error.message)}</td></tr>`;
    }
  }

  async function upload(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf"))
      return NM.toast("Only PDF files are accepted", "warning");
    uploadState.classList.remove("d-none");
    const data = new FormData();
    data.append("file", file);
    try {
      /* NM.fetch, not bare fetch: multipart uploads need the CSRF header just
         as much as JSON requests do, and FormData sets its own Content-Type. */
      const response = await NM.fetch("/api/documents", { method: "POST", body: data });
      if (!response.ok) throw new Error(await NM.readError(response));
      const body = await response.json();
      NM.toast(`Indexed "${body.document.filename}" — ${body.document.chunk_count} chunks`,
               "success");
    } catch (error) {
      NM.toast(error.message || "Upload failed", "error");
    } finally {
      uploadState.classList.add("d-none");
      fileInput.value = "";
      refresh();
    }
  }

  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => upload(fileInput.files[0]));
  ["dragover", "dragenter"].forEach((type) => drop.addEventListener(type, (e) => {
    e.preventDefault(); drop.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, (e) => {
    e.preventDefault(); drop.classList.remove("dragover");
  }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));

  rows.addEventListener("click", async (event) => {
    const reindexBtn = event.target.closest("[data-reindex]");
    const deleteBtn = event.target.closest("[data-delete]");
    if (reindexBtn) {
      reindexBtn.disabled = true;
      try {
        await NM.api(`/api/documents/${reindexBtn.dataset.reindex}/reindex`,
                     { method: "POST" });
        NM.toast("Re-indexed", "success");
      } catch (error) { NM.toast(error.message, "error"); }
      refresh();
    } else if (deleteBtn) {
      const confirmed = await NM.confirmDialog(
        "Delete this document and all its indexed chunks?");
      if (!confirmed) return;
      try {
        await NM.api(`/api/documents/${deleteBtn.dataset.delete}`, { method: "DELETE" });
        NM.toast("Document removed", "success");
      } catch (error) { NM.toast(error.message, "error"); }
      refresh();
    }
  });

  document.getElementById("kb-refresh").addEventListener("click", refresh);

  /* retrieval preview */
  document.getElementById("kb-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = document.getElementById("kb-search").value.trim();
    const out = document.getElementById("kb-search-results");
    if (!query) return;
    out.innerHTML = `<div class="nm-skeleton mb-1"></div><div class="nm-skeleton" style="width:70%"></div>`;
    try {
      const { result } = await NM.api(
        `/api/documents/search?q=${encodeURIComponent(query)}&k=3`);
      if (!result.chunks.length) {
        /* Say what is actually wrong. The old text quoted the threshold, which
           reads as "tune this number" when the real cause is usually that the
           active provider cannot match meaning, or that nothing is indexed. */
        const reason = result.semantic
          ? "Nothing in your documents was close enough in meaning."
          : "Nothing in your documents shares words with that question. This "
            + "knowledge base is using keyword matching, so try wording your "
            + "search the way the document does — or connect IBM watsonx for "
            + "semantic search.";
        out.innerHTML = `<span class="text-2">${NM.esc(reason)}
          A chat answer to this would be labeled <em>general knowledge</em>.</span>`;
        return;
      }
      out.innerHTML = result.chunks.map((c) => `
        <div class="border rounded p-2 mb-2" style="border-color:var(--nm-border)!important">
          <div class="d-flex justify-content-between mb-1">
            <span class="nm-cite"><i class="bi bi-file-earmark-text"></i>
              ${NM.esc(c.filename)} · p.${c.page}</span>
            <span class="nm-badge">${(c.similarity * 100).toFixed(0)}%</span>
          </div>
          <span class="text-2">${NM.esc(c.text.slice(0, 180))}…</span>
        </div>`).join("");
    } catch (error) { out.innerHTML = `<span class="text-2">${NM.esc(error.message)}</span>`; }
  });

  /* provider badge */
  NM.api("/api/system/info").then((info) => {
    const chroma = typeof info.storage.chroma === "object" ? info.storage.chroma : null;
    document.getElementById("kb-provider").innerHTML =
      `<i class="bi bi-cpu"></i> embeddings: ${NM.esc(chroma ? chroma.provider : "unavailable")}`;
  }).catch(() => {});

  refresh();
})();
