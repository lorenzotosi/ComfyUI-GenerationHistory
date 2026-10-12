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

function hideInternalWidgets(node) {
    for (const name of ["history_id", "persist_history"]) {
        const widget = getWidget(node, name);
        if (!widget) continue;
        widget.hidden = true;
        widget.options ??= {};
        widget.options.hidden = true;
        for (const key of ["element", "inputEl"]) {
            if (widget[key]?.style) widget[key].style.display = "none";
        }
    }
}

function ensureUniqueHistoryId(node) {
    const widget = getWidget(node, "history_id");
    const value = node._generationHistory?.historyId;
    if (!widget || !value) return false;
    if (widget.value !== value) {
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
        .gh-root { box-sizing:border-box; width:100%; height:100%; min-width:0; min-height:360px; display:flex; flex-direction:column; gap:7px; padding:7px; color:var(--input-text, #ddd); font:12px system-ui, sans-serif; background:var(--comfy-input-bg, rgba(20,20,20,.65)); border:1px solid var(--border-color, rgba(128,128,128,.35)); border-radius:7px; overflow:hidden; }
        .gh-toolbar { display:flex; align-items:center; gap:8px; min-height:28px; }
        .gh-title { font-weight:650; white-space:nowrap; }
        .gh-stats { margin-left:auto; opacity:.75; white-space:nowrap; }
        .gh-button { color:inherit; background:var(--comfy-menu-bg, rgba(90,90,90,.35)); border:1px solid var(--border-color, rgba(128,128,128,.45)); border-radius:5px; padding:3px 8px; cursor:pointer; }
        .gh-button:hover { filter:brightness(1.2); }
        .gh-clear { color:#ef9a9a; }
        .gh-scroll { box-sizing:border-box; width:100%; flex:1 1 auto; min-width:0; min-height:0; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
        .gh-empty { padding:28px 8px; text-align:center; opacity:.6; }
        .gh-row { box-sizing:border-box; width:100%; min-width:0; margin:0 0 8px; padding:6px; border:1px solid var(--border-color, rgba(128,128,128,.32)); border-radius:6px; background:rgba(127,127,127,.08); }
        .gh-header { box-sizing:border-box; width:100%; min-width:0; display:flex; align-items:flex-start; gap:7px; margin-bottom:5px; min-height:20px; }
        .gh-meta { flex:1 1 auto; min-width:0; white-space:normal; overflow-wrap:anywhere; line-height:1.35; }
        .gh-label { font-weight:700; }
        .gh-delete { margin-left:auto; flex:0 0 auto; width:24px; padding:1px 5px; font-size:15px; line-height:18px; color:#ef9a9a; }
        .gh-images-scroll { box-sizing:border-box; width:100%; min-width:0; overflow-x:auto; overflow-y:hidden; padding-bottom:4px; overscroll-behavior-x:contain; }
        .gh-images { display:flex; flex-flow:row nowrap; gap:6px; width:max-content; }
        .gh-thumb { box-sizing:border-box; width:var(--gh-thumb-size, 360px); height:var(--gh-thumb-size, 360px); flex:0 0 var(--gh-thumb-size, 360px); object-fit:contain; cursor:zoom-in; border:1px solid rgba(128,128,128,.35); border-radius:4px; background:rgba(0,0,0,.55); }
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

function imageUrl(image, thumbnail = false) {
    const query = new URLSearchParams({
        filename: thumbnail ? image.thumbnail || image.filename : image.filename,
        subfolder: image.subfolder,
        type: image.type,
    });
    return api.apiURL(`/view?${query}`);
}

async function jsonRequest(path, body, keepalive = false) {
    const response = await api.fetchApi(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive,
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
    hideInternalWidgets(node);

    const controller = new AbortController();
    const state = {
        runs: [], rows: new Map(), revision: 0, modalRun: null, modalIndex: 0,
        generation: null, requestVersion: 0, loadRequest: 0, mutating: false,
        pendingEvents: [], deletedRuns: new Set(), removed: false,
    };
    let initializedHistoryId = null;
    let initializationPromise = null;
    let mutationPromise = null;
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

    const runKey = (run) => String(run.execution_id);
    const imageKey = (image) => `${image.subfolder}/${image.filename}`;

    function updateRowSize(ref) {
        const available = ref.scroller.clientWidth;
        if (!available) return;
        const fittedSize = Math.max(160, Math.min(240, Math.floor((available - 12) / 3)));
        const size = fittedSize * 2;
        ref.row.style.setProperty("--gh-thumb-size", `${size}px`);
    }

    function updateAllRowSizes() {
        state.rows.forEach(updateRowSize);
    }

    function updateHeader(ref) {
        const { run, meta } = ref;
        const seeds = run.seeds?.length ? run.seeds : ["Unknown"];
        const seedLabel = seeds.length > 1 ? "Seeds" : "Seed";
        const loras = (run.loras || []).map((lora) =>
            `${lora.name}${lora.strength_model == null ? "" : ` @ ${lora.strength_model}`}`,
        );
        const details = [
            `${seedLabel}: ${seeds.join(", ")}`,
            `Model: ${run.model || "Unknown"}`,
            `${run.image_count ?? run.images?.length ?? 0} images`,
            formatTimestamp(run.timestamp),
        ];
        if (loras.length) details.splice(2, 0, `LoRA: ${loras.join(", ")}`);
        const content = [];
        if (run.label) {
            const label = document.createElement("span");
            label.className = "gh-label";
            label.textContent = run.label;
            content.push(label);
        }
        content.push(document.createTextNode(`${content.length ? " · " : ""}${details.filter(Boolean).join(" · ")}`));
        meta.replaceChildren(...content);
        meta.title = meta.textContent;
    }

    function appendThumbnail(ref, image, index) {
        const thumbnail = document.createElement("img");
        thumbnail.className = "gh-thumb";
        thumbnail.loading = "lazy";
        thumbnail.draggable = false;
        thumbnail.alt = `Generation image ${index + 1}`;
        thumbnail.src = imageUrl(image, true);
        thumbnail.dataset.imageKey = `${image.subfolder}/${image.filename}`;
        thumbnail.addEventListener("click", () => openModal(ref.run, index), { signal: controller.signal });
        ref.strip.append(thumbnail);
    }

    function createRow(run) {
        const row = document.createElement("section");
        row.className = "gh-row";
        row.dataset.runId = String(run.id);
        const header = document.createElement("div");
        header.className = "gh-header";
        const meta = document.createElement("div");
        meta.className = "gh-meta";

        const deleteButton = document.createElement("button");
        deleteButton.className = "gh-button gh-delete";
        deleteButton.type = "button";
        deleteButton.textContent = "×";
        deleteButton.title = "Delete generation";
        deleteButton.addEventListener("click", async () => {
            try {
                await changeHistory("/generation-history/delete-run", { run_id: run.id });
            } catch (error) {
                window.alert(`Generation History: ${error.message}`);
            }
        }, { signal: controller.signal });
        header.append(meta, deleteButton);

        const scroller = document.createElement("div");
        scroller.className = "gh-images-scroll";
        const strip = document.createElement("div");
        strip.className = "gh-images";
        scroller.append(strip);
        const ref = { run, row, meta, scroller, strip };
        updateHeader(ref);
        (run.images || []).forEach((image, index) => appendThumbnail(ref, image, index));
        row.append(header, scroller);
        state.rows.set(runKey(run), ref);
        requestAnimationFrame(() => updateRowSize(ref));
        return ref;
    }

    function renderAll(runs) {
        state.runs = [...runs].sort((a, b) =>
            (a.id - b.id) || (new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
        );
        if (state.modalRun) {
            const key = runKey(state.modalRun);
            state.modalRun = state.runs.find((run) => runKey(run) === key) || null;
            updateModal();
        }
        state.rows.clear();
        scroll.replaceChildren();
        if (!state.runs.length) {
            scroll.innerHTML = '<div class="gh-empty">No generations yet</div>';
        } else {
            scroll.append(...state.runs.map((run) => createRow(run).row));
        }
        updateStats();
    }

    function appendRun(run) {
        if (state.runs.some((item) => runKey(item) === runKey(run))) return;
        state.runs.push(run);
        scroll.querySelector(".gh-empty")?.remove();
        scroll.append(createRow(run).row);
        requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
        updateStats();
    }

    function updateRun(run, newImages = []) {
        const ref = state.rows.get(runKey(run));
        if (!ref) {
            if ((run.images?.length || 0) < Number(run.image_count || 0)) {
                loadHistory(true);
                return;
            }
            appendRun(run);
            return;
        }
        const knownImages = new Set(
            (ref.run.images || []).map((image) => `${image.subfolder}/${image.filename}`),
        );
        const mergedImages = [...(ref.run.images || [])];
        newImages.forEach((image) => {
            const key = `${image.subfolder}/${image.filename}`;
            if (!knownImages.has(key)) {
                knownImages.add(key);
                mergedImages.push(image);
            }
        });
        run = { ...run, images: mergedImages };
        const index = state.runs.findIndex((item) => runKey(item) === runKey(run));
        if (index >= 0) state.runs[index] = run;
        ref.run = run;
        const existing = new Set([...ref.strip.children].map((image) => image.dataset.imageKey));
        newImages.forEach((image) => {
            const key = `${image.subfolder}/${image.filename}`;
            if (!existing.has(key)) appendThumbnail(ref, image, run.images.findIndex((item) => `${item.subfolder}/${item.filename}` === key));
        });
        if (state.modalRun && runKey(state.modalRun) === runKey(run)) state.modalRun = run;
        updateHeader(ref);
        updateRowSize(ref);
        updateStats();
    }

    async function loadHistory(mergeCurrent = false) {
        const historyId = String(getWidget(node, "history_id")?.value || "");
        if (!UUID_RE.test(historyId)) return;
        const revision = state.revision;
        const version = state.requestVersion;
        const request = ++state.loadRequest;
        try {
            const response = await api.fetchApi(`/generation-history/history?id=${encodeURIComponent(historyId)}`);
            const data = await response.json();
            if (state.removed || version !== state.requestVersion || request !== state.loadRequest) return;
            if (!response.ok) throw new Error(data.error || response.statusText);
            if (data.generation !== state.generation) return;
            if (!mergeCurrent && revision === state.revision) {
                renderAll(data.runs || []);
            } else {
                const merged = new Map((data.runs || []).map((run) => [runKey(run), run]));
                state.runs.forEach((run) => {
                    const serverRun = merged.get(runKey(run));
                    const images = new Map((serverRun?.images || []).map((image) => [imageKey(image), image]));
                    (run.images || []).forEach((image) => images.set(imageKey(image), image));
                    const ordered = [...images.values()].sort((a, b) => a.index - b.index);
                    merged.set(runKey(run), {
                        ...(Number(serverRun?.image_count || 0) > Number(run.image_count || 0) ? serverRun : run),
                        images: ordered,
                        image_count: ordered.length,
                    });
                });
                renderAll([...merged.values()]);
            }
        } catch (error) {
            if (state.removed || version !== state.requestVersion || request !== state.loadRequest) return;
            scroll.innerHTML = `<div class="gh-error"></div>`;
            scroll.firstElementChild.textContent = `Unable to load history: ${error.message}`;
        }
    }

    function initializeHistory() {
        const historyId = String(getWidget(node, "history_id")?.value || "");
        if (state.removed || !UUID_RE.test(historyId)) return Promise.resolve();
        if (initializedHistoryId === historyId && initializationPromise) {
            return initializationPromise;
        }

        initializedHistoryId = historyId;
        state.generation = newUuid().replaceAll("-", "");
        state.requestVersion += 1;
        state.mutating = true;
        const generation = state.generation;
        initializationPromise = (async () => {
            await jsonRequest("/generation-history/reset", {
                history_id: historyId,
                generation,
            });
            if (state.removed || state.generation !== generation) return;
            state.revision = 0;
            state.deletedRuns.clear();
            closeModal();
            renderAll([]);
            await loadHistory();
        })().catch((error) => {
            if (state.removed || state.generation !== generation) return;
            scroll.innerHTML = '<div class="gh-error"></div>';
            scroll.firstElementChild.textContent = `Unable to initialize history: ${error.message}`;
        }).finally(() => {
            if (state.generation === generation) finishMutation();
        });
        return initializationPromise;
    }

    function finishMutation() {
        state.mutating = false;
        const pending = state.pendingEvents;
        state.pendingEvents = [];
        pending.forEach(eventHandler);
    }

    function changeHistory(path, body = {}, clearAll = false) {
        if (state.removed) return Promise.resolve();
        if (state.mutating) {
            return Promise.allSettled([initializationPromise, mutationPromise])
                .then(() => changeHistory(path, body, clearAll));
        }
        state.requestVersion += 1;
        state.mutating = true;
        const generation = clearAll ? newUuid().replaceAll("-", "") : state.generation;
        mutationPromise = (async () => {
            await jsonRequest(path, {
                ...body, history_id: getWidget(node, "history_id")?.value,
                ...(clearAll ? { generation } : {}),
            });
            if (clearAll) {
                state.generation = generation;
                state.deletedRuns.clear();
            } else {
                state.deletedRuns.add(String(body.run_id));
            }
            if (state.removed) return;
            renderAll(clearAll ? [] : state.runs.filter((run) => String(run.id) !== String(body.run_id)));
            closeModal();
            if (!state.removed) await loadHistory();
        })().finally(finishMutation);
        return mutationPromise;
    }

    root.querySelector(".gh-clear").addEventListener("click", async () => {
        if (!window.confirm("Clear all generation history?")) return;
        try {
            await changeHistory("/generation-history/clear", {}, true);
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
        if (state.removed) return;
        const payload = event.detail;
        if (!payload?.run) return;
        let historyId = String(getWidget(node, "history_id")?.value || "");
        if (!UUID_RE.test(historyId) && String(payload.node_id) === String(node.id)) {
            getWidget(node, "history_id").value = payload.history_id;
            historyId = payload.history_id;
        }
        if (payload.history_id !== historyId) return;
        if (state.mutating) {
            state.pendingEvents.push(event);
            return;
        }
        if (payload.run.generation !== state.generation || state.deletedRuns.has(String(payload.run.id))) return;
        state.revision += 1;
        updateRun(payload.run, payload.new_images || []);
    };
    api.addEventListener(EVENT_NAME, eventHandler);
    window.addEventListener("pagehide", (event) => {
        if (event.persisted || state.removed || !initializedHistoryId) return;
        jsonRequest("/generation-history/release", {
            history_id: initializedHistoryId, generation: state.generation,
        }, true).catch((error) => console.error(`Generation History: ${error.message}`));
    }, { signal: controller.signal });

    node.addDOMWidget("generation_history_gallery", "generation_history", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 360,
        getHeight: () => "100%",
        afterResize: () => requestAnimationFrame(updateAllRowSizes),
    });
    if (!node.size || node.size[0] < 500 || node.size[1] < 500) {
        node.setSize?.([Math.max(node.size?.[0] || 0, 700), Math.max(node.size?.[1] || 0, 500)]);
    }

    const previousRemoved = node.onRemoved;
    const resizeObserver = new ResizeObserver(() => updateAllRowSizes());
    resizeObserver.observe(root);
    node.onRemoved = function () {
        state.removed = true;
        state.requestVersion += 1;
        const historyId = initializedHistoryId;
        Promise.allSettled([initializationPromise, mutationPromise]).then(() => {
            if (!UUID_RE.test(historyId) || !state.generation) return;
            return jsonRequest("/generation-history/release", {
                history_id: historyId, generation: state.generation,
            });
        }).catch((error) => console.error(`Generation History: ${error.message}`));
        api.removeEventListener(EVENT_NAME, eventHandler);
        resizeObserver.disconnect();
        controller.abort();
        state.pendingEvents = [];
        state.rows.clear();
        state.runs = [];
        closeModal();
        modal.remove();
        node.onAdded = function () {
            const result = previousAdded?.apply(this, arguments);
            node.onAdded = previousAdded;
            node.onRemoved = previousRemoved;
            node._generationHistory = null;
            node.widgets = node.widgets.filter((widget) => widget.name !== "generation_history_gallery");
            root.remove();
            setupNode(node);
            return result;
        };
        previousRemoved?.apply(this, arguments);
    };
    const previousAdded = node.onAdded;
    node.onAdded = function () {
        const result = previousAdded?.apply(this, arguments);
        queueMicrotask(() => {
            if (state.removed) return;
            ensureUniqueHistoryId(node);
            initializeHistory();
        });
        return result;
    };

    node._generationHistory = { historyId: newUuid(), reload: () => initializeHistory() };
    queueMicrotask(() => {
        if (state.removed) return;
        ensureUniqueHistoryId(node);
        initializeHistory();
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
            hideInternalWidgets(node);
            ensureUniqueHistoryId(node);
            node._generationHistory?.reload();
        }
    },
});
