import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const NODE_CLASS = "GenerationHistory";
const EVENT_NAME = "generation_history.run_added";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function newUuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (digit) =>
        (Number(digit) ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(digit) / 4)))).toString(16),
    );
}

function getWidget(node, name) {
    return node.widgets?.find((widget) => widget.name === name);
}

function hideHistoryIdWidget(node) {
    const widget = getWidget(node, "history_id");
    if (!widget) return;
    widget.hidden = true;
    widget.options ??= {};
    widget.options.hidden = true;
    for (const key of ["element", "inputEl"]) {
        if (widget[key]?.style) widget[key].style.display = "none";
    }
}

function ensureUniqueHistoryId(node) {
    const widget = getWidget(node, "history_id");
    if (!widget) return false;

    let value = String(widget.value || "");
    const nodes = app.graph?._nodes || [];
    const index = nodes.indexOf(node);
    const duplicateBefore = nodes.slice(0, Math.max(0, index)).some((other) =>
        other?.comfyClass === NODE_CLASS && String(getWidget(other, "history_id")?.value || "") === value,
    );
    if (!UUID_RE.test(value) || duplicateBefore) {
        value = newUuid();
        widget.value = value;
        node.setDirtyCanvas?.(true, true);
        return true;
    }
    return false;
}

function injectStyles() {
    if (document.getElementById("generation-history-styles")) return;
    const style = document.createElement("style");
    style.id = "generation-history-styles";
    style.textContent = `
        .gh-root { box-sizing:border-box; width:100%; height:100%; min-height:360px; display:flex; flex-direction:column; gap:7px; padding:7px; color:var(--input-text, #ddd); font:12px system-ui, sans-serif; background:var(--comfy-input-bg, rgba(20,20,20,.65)); border:1px solid var(--border-color, rgba(128,128,128,.35)); border-radius:7px; overflow:hidden; }
        .gh-toolbar { display:flex; align-items:center; gap:8px; min-height:28px; }
        .gh-title { font-weight:650; white-space:nowrap; }
        .gh-stats { margin-left:auto; opacity:.75; white-space:nowrap; }
        .gh-button { color:inherit; background:var(--comfy-menu-bg, rgba(90,90,90,.35)); border:1px solid var(--border-color, rgba(128,128,128,.45)); border-radius:5px; padding:3px 8px; cursor:pointer; }
        .gh-button:hover { filter:brightness(1.2); }
        .gh-clear { color:#ef9a9a; }
        .gh-scroll { flex:1 1 auto; min-height:0; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
        .gh-empty { padding:28px 8px; text-align:center; opacity:.6; }
        .gh-row { margin:0 0 8px; padding:6px; border:1px solid var(--border-color, rgba(128,128,128,.32)); border-radius:6px; background:rgba(127,127,127,.08); }
        .gh-header { display:flex; align-items:center; gap:7px; margin-bottom:5px; min-height:20px; }
        .gh-meta { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .gh-run { font-weight:700; }
        .gh-delete { margin-left:auto; flex:0 0 auto; width:24px; padding:1px 5px; font-size:15px; line-height:18px; color:#ef9a9a; }
        .gh-images { display:flex; flex-flow:row nowrap; gap:6px; width:100%; overflow-x:auto; overflow-y:hidden; padding-bottom:4px; overscroll-behavior-x:contain; }
        .gh-thumb { box-sizing:border-box; width:120px; height:120px; flex:0 0 120px; object-fit:contain; cursor:zoom-in; border:1px solid rgba(128,128,128,.35); border-radius:4px; background:rgba(0,0,0,.55); }
        .gh-error { color:#ef9a9a; padding:8px; }
        .gh-modal { position:fixed; inset:0; z-index:100000; display:flex; align-items:center; justify-content:center; background:rgba(0,0,0,.88); }
        .gh-modal[hidden] { display:none; }
        .gh-modal-image { max-width:calc(100vw - 100px); max-height:calc(100vh - 60px); object-fit:contain; }
        .gh-modal-button { position:fixed; z-index:1; width:42px; height:42px; border:0; border-radius:50%; color:#fff; background:rgba(40,40,40,.75); font-size:26px; cursor:pointer; }
        .gh-modal-close { top:16px; right:18px; }
        .gh-modal-prev { left:18px; top:50%; }
        .gh-modal-next { right:18px; top:50%; }
    `;
    document.head.append(style);
}

function imageUrl(image) {
    const query = new URLSearchParams({
        filename: image.filename,
        subfolder: image.subfolder,
        type: image.type,
    });
    return api.apiURL(`/view?${query}`);
}

async function jsonRequest(path, body) {
    const response = await api.fetchApi(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
    return data;
}

function formatTimestamp(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function setupNode(node) {
    if (node._generationHistory) return;
    injectStyles();
    hideHistoryIdWidget(node);

    const controller = new AbortController();
    const state = { runs: [], revision: 0, modalRun: null, modalIndex: 0 };
    const root = document.createElement("div");
    root.className = "gh-root";
    root.innerHTML = `
        <div class="gh-toolbar">
            <span class="gh-title">Generation History</span>
            <span class="gh-stats">0 runs · 0 images</span>
            <button class="gh-button gh-clear" type="button">Clear</button>
        </div>
        <div class="gh-scroll"><div class="gh-empty">No generations yet</div></div>
    `;
    const scroll = root.querySelector(".gh-scroll");
    const stats = root.querySelector(".gh-stats");

    const modal = document.createElement("div");
    modal.className = "gh-modal";
    modal.hidden = true;
    modal.innerHTML = `
        <button class="gh-modal-button gh-modal-close" type="button" aria-label="Close">×</button>
        <button class="gh-modal-button gh-modal-prev" type="button" aria-label="Previous image">‹</button>
        <img class="gh-modal-image" alt="Full resolution generation">
        <button class="gh-modal-button gh-modal-next" type="button" aria-label="Next image">›</button>
    `;
    document.body.append(modal);
    const modalImage = modal.querySelector(".gh-modal-image");

    function updateStats() {
        const images = state.runs.reduce((sum, run) => sum + Number(run.image_count || run.images?.length || 0), 0);
        stats.textContent = `${state.runs.length} runs · ${images} images`;
    }

    function updateModal() {
        const images = state.modalRun?.images || [];
        if (!images.length) return closeModal();
        state.modalIndex = (state.modalIndex + images.length) % images.length;
        modalImage.src = imageUrl(images[state.modalIndex]);
        modal.querySelector(".gh-modal-prev").hidden = images.length < 2;
        modal.querySelector(".gh-modal-next").hidden = images.length < 2;
    }

    function openModal(run, index) {
        state.modalRun = run;
        state.modalIndex = index;
        modal.hidden = false;
        updateModal();
    }

    function closeModal() {
        modal.hidden = true;
        modalImage.removeAttribute("src");
        state.modalRun = null;
    }

    function createRow(run) {
        const row = document.createElement("section");
        row.className = "gh-row";
        row.dataset.runId = String(run.id);
        const header = document.createElement("div");
        header.className = "gh-header";
        const meta = document.createElement("div");
        meta.className = "gh-meta";
        const loras = (run.loras || []).map((lora) =>
            `${lora.name}${lora.strength_model == null ? "" : ` @ ${lora.strength_model}`}`,
        );
        const parts = [
            `Run #${run.id}${run.label ? ` · ${run.label}` : ""}`,
            `Seed: ${run.seed ?? "Unknown"}`,
            `Model: ${run.model || "Unknown"}`,
            `${run.image_count ?? run.images?.length ?? 0} images`,
            formatTimestamp(run.timestamp),
        ];
        if (loras.length) parts.splice(3, 0, `LoRA: ${loras.join(", ")}`);
        const runTitle = document.createElement("span");
        runTitle.className = "gh-run";
        runTitle.textContent = parts.shift();
        meta.append(runTitle, document.createTextNode(` · ${parts.filter(Boolean).join(" · ")}`));
        meta.title = meta.textContent;

        const deleteButton = document.createElement("button");
        deleteButton.className = "gh-button gh-delete";
        deleteButton.type = "button";
        deleteButton.textContent = "×";
        deleteButton.title = `Delete Run #${run.id}`;
        deleteButton.addEventListener("click", async () => {
            try {
                await jsonRequest("/generation-history/delete-run", {
                    history_id: getWidget(node, "history_id")?.value,
                    run_id: run.id,
                });
                state.runs = state.runs.filter((item) => item.id !== run.id);
                row.remove();
                if (!state.runs.length) scroll.innerHTML = '<div class="gh-empty">No generations yet</div>';
                updateStats();
            } catch (error) {
                window.alert(`Generation History: ${error.message}`);
            }
        }, { signal: controller.signal });
        header.append(meta, deleteButton);

        const strip = document.createElement("div");
        strip.className = "gh-images";
        (run.images || []).forEach((image, index) => {
            const thumbnail = document.createElement("img");
            thumbnail.className = "gh-thumb";
            thumbnail.loading = "lazy";
            thumbnail.draggable = false;
            thumbnail.alt = `Run ${run.id}, image ${index + 1}`;
            thumbnail.src = imageUrl(image);
            thumbnail.addEventListener("click", () => openModal(run, index), { signal: controller.signal });
            strip.append(thumbnail);
        });
        row.append(header, strip);
        return row;
    }

    function renderAll(runs) {
        state.runs = [...runs].sort((a, b) => b.id - a.id);
        scroll.replaceChildren();
        if (!state.runs.length) {
            scroll.innerHTML = '<div class="gh-empty">No generations yet</div>';
        } else {
            scroll.append(...state.runs.map(createRow));
        }
        updateStats();
    }

    function prependRun(run) {
        if (state.runs.some((item) => item.id === run.id)) return;
        state.runs.unshift(run);
        scroll.querySelector(".gh-empty")?.remove();
        scroll.prepend(createRow(run));
        scroll.scrollTop = 0;
        updateStats();
    }

    async function loadHistory() {
        const historyId = String(getWidget(node, "history_id")?.value || "");
        if (!UUID_RE.test(historyId)) return;
        const persist = Boolean(getWidget(node, "persist_history")?.value);
        const revision = state.revision;
        try {
            const response = await api.fetchApi(`/generation-history/history?id=${encodeURIComponent(historyId)}&persist=${persist ? 1 : 0}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || response.statusText);
            if (revision === state.revision) {
                renderAll(data.runs || []);
            } else {
                const merged = new Map((data.runs || []).map((run) => [run.id, run]));
                state.runs.forEach((run) => merged.set(run.id, run));
                renderAll([...merged.values()]);
            }
        } catch (error) {
            scroll.innerHTML = `<div class="gh-error"></div>`;
            scroll.firstElementChild.textContent = `Unable to load history: ${error.message}`;
        }
    }

    root.querySelector(".gh-clear").addEventListener("click", async () => {
        if (!window.confirm("Clear all generation history?")) return;
        try {
            await jsonRequest("/generation-history/clear", {
                history_id: getWidget(node, "history_id")?.value,
            });
            renderAll([]);
        } catch (error) {
            window.alert(`Generation History: ${error.message}`);
        }
    }, { signal: controller.signal });

    modal.querySelector(".gh-modal-close").addEventListener("click", closeModal, { signal: controller.signal });
    modal.querySelector(".gh-modal-prev").addEventListener("click", () => {
        state.modalIndex -= 1;
        updateModal();
    }, { signal: controller.signal });
    modal.querySelector(".gh-modal-next").addEventListener("click", () => {
        state.modalIndex += 1;
        updateModal();
    }, { signal: controller.signal });
    modal.addEventListener("click", (event) => {
        if (event.target === modal) closeModal();
    }, { signal: controller.signal });
    window.addEventListener("keydown", (event) => {
        if (modal.hidden) return;
        if (event.key === "Escape") closeModal();
        if (event.key === "ArrowLeft") { state.modalIndex -= 1; updateModal(); }
        if (event.key === "ArrowRight") { state.modalIndex += 1; updateModal(); }
    }, { signal: controller.signal });

    for (const eventName of ["pointerdown", "pointerup", "click", "dblclick", "contextmenu", "wheel"]) {
        root.addEventListener(eventName, (event) => event.stopPropagation(), {
            signal: controller.signal,
            passive: eventName === "wheel",
        });
    }

    const eventHandler = (event) => {
        const payload = event.detail;
        if (!payload?.run) return;
        let historyId = String(getWidget(node, "history_id")?.value || "");
        if (!UUID_RE.test(historyId) && String(payload.node_id) === String(node.id)) {
            getWidget(node, "history_id").value = payload.history_id;
            historyId = payload.history_id;
        }
        if (payload.history_id !== historyId) return;
        state.revision += 1;
        prependRun(payload.run);
    };
    api.addEventListener(EVENT_NAME, eventHandler);

    node.addDOMWidget("generation_history_gallery", "generation_history", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 360,
        getHeight: () => 420,
    });
    if (!node.size || node.size[0] < 500 || node.size[1] < 500) {
        node.setSize?.([Math.max(node.size?.[0] || 0, 700), Math.max(node.size?.[1] || 0, 500)]);
    }

    const previousRemoved = node.onRemoved;
    node.onRemoved = function () {
        api.removeEventListener(EVENT_NAME, eventHandler);
        controller.abort();
        modal.remove();
        previousRemoved?.apply(this, arguments);
    };
    const previousAdded = node.onAdded;
    node.onAdded = function () {
        const result = previousAdded?.apply(this, arguments);
        queueMicrotask(() => {
            ensureUniqueHistoryId(node);
            loadHistory();
        });
        return result;
    };

    node._generationHistory = { reload: loadHistory };
    queueMicrotask(() => {
        ensureUniqueHistoryId(node);
        loadHistory();
    });
}

app.registerExtension({
    name: "GenerationHistory.Gallery",
    async nodeCreated(node) {
        if (node.comfyClass === NODE_CLASS) setupNode(node);
    },
    async afterConfigureGraph() {
        for (const node of app.graph?._nodes || []) {
            if (node.comfyClass !== NODE_CLASS) continue;
            hideHistoryIdWidget(node);
            if (ensureUniqueHistoryId(node)) node._generationHistory?.reload();
        }
    },
});
