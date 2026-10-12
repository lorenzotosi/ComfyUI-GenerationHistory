const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/js/generation_history.js"), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(
        "node._generationHistory = { historyId: newUuid(), reload: () => initializeHistory() };",
        "node._generationHistory = { historyId: newUuid(), reload: () => initializeHistory(), state, loadHistory, changeHistory, eventHandler };",
    );

class Element {
    constructor() {
        this.children = [];
        this.selectors = new Map();
        this.listeners = new Map();
        this.style = { setProperty() {} };
        this.dataset = {};
        this.clientWidth = 900;
    }
    set innerHTML(value) {
        this.children = value ? [new Element()] : [];
    }
    get firstElementChild() { return this.children[0]; }
    querySelector(selector) {
        if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
        return this.selectors.get(selector);
    }
    append(...children) {
        children.forEach((child) => { child.parent = this; });
        this.children.push(...children);
    }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    remove() {
        if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    }
    removeAttribute() {}
    addEventListener(name, callback, options) {
        this.listeners.set(name, callback);
        options?.signal?.addEventListener("abort", () => this.listeners.delete(name), { once: true });
    }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function gallery(
    server = { history: { runs: [] }, delayedReads: [], delayReads: false, failMutation: false },
    historyId = webcrypto.randomUUID(),
) {
    const requests = [];
    const listeners = new Map();
    const windowEvents = new Map();
    server.histories ??= new Map();
    function currentHistoryId() {
        return node.widgets.find((widget) => widget.name === "history_id").value;
    }
    function currentHistory() {
        return server.histories.get(currentHistoryId());
    }
    let extension;
    const app = { graph: { _nodes: [] }, registerExtension: (value) => { extension = value; } };
    const api = {
        apiURL: (url) => url,
        addEventListener: (name, callback) => listeners.set(name, callback),
        removeEventListener: (name) => listeners.delete(name),
        fetchApi: async (url, options) => {
            const body = options?.body ? JSON.parse(options.body) : null;
            requests.push({ url, body, keepalive: options?.keepalive });
            if (url.startsWith("/generation-history/history")) {
                const historyId = new URLSearchParams(url.split("?")[1]).get("id");
                const snapshot = structuredClone(server.histories.get(historyId));
                if (server.delayReads) {
                    return new Promise((resolve) => server.delayedReads.push(() => resolve({ ok: true, json: async () => snapshot })));
                }
                return { ok: true, json: async () => snapshot };
            }
            if (server.failMutation) return { ok: false, json: async () => ({ error: "locked" }) };
            if (url.endsWith("/reset") || url.endsWith("/clear")) {
                server.history = { generation: body.generation, runs: [] };
                server.histories.set(body.history_id, server.history);
            }
            const history = server.histories.get(body.history_id);
            if (url.endsWith("/delete-run")) history.runs = history.runs.filter((run) => run.id !== body.run_id);
            if (url.endsWith("/release") && history?.generation === body.generation) history.runs = [];
            return { ok: true, json: async () => ({ ok: true }) };
        },
    };
    const context = {
        app, api, crypto: webcrypto, AbortController, URLSearchParams, console,
        queueMicrotask, requestAnimationFrame: (callback) => callback(),
        ResizeObserver: class { observe() {} disconnect() {} },
        document: {
            getElementById: () => null, createElement: () => new Element(),
            createTextNode: (textContent) => ({ textContent }), head: new Element(), body: new Element(),
        },
        window: {
            confirm: () => true, alert() {},
            addEventListener(name, callback, options) {
                windowEvents.set(name, callback);
                options?.signal?.addEventListener("abort", () => windowEvents.delete(name), { once: true });
            },
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    const node = {
        id: 1, comfyClass: "GenerationHistory", size: [700, 500],
        widgets: [{ name: "history_id", value: historyId }],
        addDOMWidget(name, type, element) { this.widgets.push({ name, element }); },
        setDirtyCanvas() {},
    };
    app.graph._nodes.push(node);
    await extension.nodeCreated(node);
    await flush();
    function run(count, id = 1) {
        return {
            id, execution_id: `prompt-${id}`, generation: node._generationHistory.state.generation,
            seeds: [1], model: "model", loras: [], timestamp: "2026-10-12T12:00:00Z",
            image_count: count, images: Array.from({ length: count }, (_, index) => ({
                index, filename: `image_${index}.png`, thumbnail: `image_${index}.thumb.jpg`,
                subfolder: `gallery-${id}`, type: "temp",
            })),
        };
    }
    function emit(value, historyId = currentHistoryId()) {
        const payload = {
            history_id: historyId,
            run: value, new_images: value.images,
        };
        listeners.get("generation_history.run_added")?.({ detail: payload });
    }
    return { node, server, requests, listeners, windowEvents, app, extension, run, emit, currentHistoryId, currentHistory, document: context.document };
}

test("recovery merges full server images with local deltas in index order", async () => {
    const g = await gallery();
    g.emit(g.run(1));
    g.server.history.runs = [g.run(3)];
    await g.node._generationHistory.loadHistory(true);
    const run = g.node._generationHistory.state.runs[0];
    assert.equal(run.image_count, 3);
    assert.deepEqual(Array.from(run.images, (image) => image.index), [0, 1, 2]);
});

test("Clear invalidates old HTTP responses and late events", async () => {
    const g = await gallery();
    const oldRun = g.run(1);
    g.emit(oldRun);
    g.server.history.runs = [oldRun];
    g.server.delayReads = true;
    const pending = g.node._generationHistory.loadHistory();
    g.server.delayReads = false;
    await g.node._generationHistory.changeHistory("/generation-history/clear", {}, true);
    g.server.delayedReads.shift()();
    await pending;
    g.emit(oldRun);
    assert.equal(g.node._generationHistory.state.runs.length, 0);
});

test("Delete invalidates old responses and events for the deleted run", async () => {
    const g = await gallery();
    const oldRun = g.run(1);
    g.emit(oldRun);
    g.server.history.runs = [oldRun];
    g.server.delayReads = true;
    const pending = g.node._generationHistory.loadHistory(true);
    g.server.delayReads = false;
    await g.node._generationHistory.changeHistory("/generation-history/delete-run", { run_id: 1 });
    g.server.delayedReads.shift()();
    await pending;
    g.emit(oldRun);
    assert.equal(g.node._generationHistory.state.runs.length, 0);
});

test("an older read cannot overwrite a newer completed read", async () => {
    const g = await gallery();
    g.server.history.runs = [g.run(1)];
    g.server.delayReads = true;
    const older = g.node._generationHistory.loadHistory();
    g.server.history.runs = [g.run(2)];
    const newer = g.node._generationHistory.loadHistory();
    g.server.delayedReads.pop()();
    await newer;
    g.server.delayedReads.pop()();
    await older;
    assert.equal(g.node._generationHistory.state.runs[0].images.length, 2);
});

test("events arriving during a mutation are replayed after the snapshot", async () => {
    const g = await gallery();
    g.server.delayReads = true;
    const clearing = g.node._generationHistory.changeHistory("/generation-history/clear", {}, true);
    await flush();
    g.emit(g.run(1, 2));
    g.server.delayedReads.shift()();
    await clearing;
    assert.equal(g.node._generationHistory.state.runs[0].id, 2);
});

test("failed deletion keeps the run visible and allows a retry", async () => {
    const g = await gallery();
    g.emit(g.run(1));
    g.server.history.runs = [g.run(1)];
    g.server.failMutation = true;
    await assert.rejects(g.node._generationHistory.changeHistory("/generation-history/delete-run", { run_id: 1 }), /locked/);
    assert.equal(g.node._generationHistory.state.runs.length, 1);
    g.server.failMutation = false;
    await g.node._generationHistory.changeHistory("/generation-history/delete-run", { run_id: 1 });
    assert.equal(g.node._generationHistory.state.runs.length, 0);
});

test("removal releases server resources and undo creates a working gallery", async () => {
    const g = await gallery();
    const oldGeneration = g.node._generationHistory.state.generation;
    g.emit(g.run(1));
    g.node.onRemoved();
    await flush();
    assert.equal(g.requests.filter((request) => request.url.endsWith("/release")).length, 1);
    assert.equal(g.listeners.size, 0);
    g.node.onAdded();
    await flush();
    assert.notEqual(g.node._generationHistory.state.generation, oldGeneration);
    assert.equal(g.node.widgets.filter((widget) => widget.name === "generation_history_gallery").length, 1);
    g.emit(g.run(1));
    assert.equal(g.node._generationHistory.state.runs.length, 1);
});

test("a pending read cannot render after removal", async () => {
    const g = await gallery();
    const state = g.node._generationHistory.state;
    g.server.history.runs = [g.run(1)];
    g.server.delayReads = true;
    const pending = g.node._generationHistory.loadHistory();
    g.node.onRemoved();
    g.server.delayedReads.shift()();
    await pending;
    await flush();
    assert.equal(state.runs.length, 0);
    assert.equal(state.rows.size, 0);
});

test("rows load small thumbnails while the viewer loads the original PNG", async () => {
    const g = await gallery();
    g.emit(g.run(1));
    const row = g.node._generationHistory.state.rows.get("prompt-1");
    const thumbnail = row.strip.children[0];
    assert.match(thumbnail.src, /filename=image_0.thumb.jpg/);
    thumbnail.listeners.get("click")();
    const modalImage = g.document.body.children.at(-1).querySelector(".gh-modal-image");
    assert.match(modalImage.src, /filename=image_0.png/);
});

test("workflow configuration keeps its runtime UUID when restoring a saved UUID", async () => {
    const g = await gallery();
    const oldId = g.node.widgets.find((widget) => widget.name === "history_id").value;
    const restoredId = webcrypto.randomUUID();
    g.node.widgets.find((widget) => widget.name === "history_id").value = restoredId;
    await g.extension.afterConfigureGraph();
    await flush();
    assert.notEqual(g.currentHistoryId(), restoredId);
    assert.equal(g.currentHistoryId(), oldId);
    g.emit(g.run(1));
    assert.equal(g.node._generationHistory.state.runs.length, 1);
    const resets = g.requests.filter((request) => request.url.endsWith("/reset")).length;
    await g.extension.afterConfigureGraph();
    await flush();
    assert.equal(g.requests.filter((request) => request.url.endsWith("/reset")).length, resets);
});

test("opening the same workflow in another tab keeps the first tab receiving runs", async () => {
    const first = await gallery();
    const historyId = first.node.widgets.find((widget) => widget.name === "history_id").value;
    const second = await gallery(first.server, historyId);
    const firstRun = first.run(2);
    firstRun.generation = first.currentHistory().generation;
    first.currentHistory().runs = [firstRun];
    first.emit(firstRun);
    second.emit(second.run(1));
    await flush();
    assert.equal(first.node._generationHistory.state.runs.length, 1);
    assert.equal(first.node._generationHistory.state.runs[0].images.length, 2);
    assert.equal(second.node._generationHistory.state.runs.length, 1);
    assert.notEqual(first.currentHistoryId(), second.currentHistoryId());
    first.emit(second.run(1, 2), second.currentHistoryId());
    assert.equal(first.node._generationHistory.state.runs.length, 1);
});

test("history reads stay valid after opening the same workflow in another tab", async () => {
    const first = await gallery();
    const historyId = first.node.widgets.find((widget) => widget.name === "history_id").value;
    const second = await gallery(first.server, historyId);
    const run = first.run(1);
    run.generation = first.currentHistory().generation;
    first.currentHistory().runs = [run];
    await first.node._generationHistory.loadHistory(true);
    assert.equal(first.node._generationHistory.state.generation, first.currentHistory().generation);
    assert.equal(first.node._generationHistory.state.runs.length, 1);
});

test("Clear confirmed during Delete waits and then clears the remaining runs", async () => {
    const g = await gallery();
    const first = g.run(1, 1);
    const second = g.run(1, 2);
    g.emit(first);
    g.emit(second);
    g.server.history.runs = [first, second];
    g.server.delayReads = true;
    const deleteButton = g.node._generationHistory.state.rows.get("prompt-1").row.children[0].children[1];
    const deleting = deleteButton.listeners.get("click")();
    await flush();
    const root = g.node.widgets.find((widget) => widget.name === "generation_history_gallery").element;
    const clearing = root.querySelector(".gh-clear").listeners.get("click")();
    g.server.delayReads = false;
    g.server.delayedReads.shift()();
    await Promise.all([deleting, clearing]);
    assert.equal(g.requests.filter((request) => request.url.endsWith("/clear")).length, 1);
    assert.equal(g.server.history.runs.length, 0);
    assert.equal(g.node._generationHistory.state.runs.length, 0);
});

test("Clear and release in one tab leave another tab's gallery intact", async () => {
    const first = await gallery();
    const savedId = first.currentHistoryId();
    const second = await gallery(first.server, savedId);
    const run = second.run(1);
    second.currentHistory().runs = [run];
    second.emit(run);
    await first.node._generationHistory.changeHistory("/generation-history/clear", {}, true);
    first.node.onRemoved();
    await flush();
    assert.equal(second.currentHistory().runs.length, 1);
    second.emit(second.run(1, 2));
    assert.equal(second.node._generationHistory.state.runs.length, 2);
});

test("rapid deletes of different rows both reach the server", async () => {
    const g = await gallery();
    const first = g.run(1, 1);
    const second = g.run(1, 2);
    g.emit(first);
    g.emit(second);
    g.server.history.runs = [first, second];
    g.server.delayReads = true;
    const firstButton = g.node._generationHistory.state.rows.get("prompt-1").row.children[0].children[1];
    const deletingFirst = firstButton.listeners.get("click")();
    await flush();
    const secondButton = g.node._generationHistory.state.rows.get("prompt-2").row.children[0].children[1];
    const deletingSecond = secondButton.listeners.get("click")();
    g.server.delayReads = false;
    g.server.delayedReads.shift()();
    await Promise.all([deletingFirst, deletingSecond]);
    assert.deepEqual(g.requests.filter((request) => request.url.endsWith("/delete-run")).map((request) => request.body.run_id), [1, 2]);
    assert.equal(g.server.history.runs.length, 0);
    assert.equal(g.node._generationHistory.state.runs.length, 0);
});

test("tab unload releases only its own history with a keepalive request", async () => {
    const first = await gallery();
    const second = await gallery(first.server, first.currentHistoryId());
    first.currentHistory().runs = [first.run(1)];
    second.currentHistory().runs = [second.run(1)];
    first.windowEvents.get("pagehide")({ persisted: false });
    await flush();
    assert.equal(first.currentHistory().runs.length, 0);
    assert.equal(second.currentHistory().runs.length, 1);
    const release = first.requests.find((request) => request.url.endsWith("/release"));
    assert.equal(release.keepalive, true);
});

test("browser back-forward cache keeps history and removed nodes unregister pagehide", async () => {
    const g = await gallery();
    g.currentHistory().runs = [g.run(1)];
    g.windowEvents.get("pagehide")({ persisted: true });
    await flush();
    assert.equal(g.currentHistory().runs.length, 1);
    assert.equal(g.requests.filter((request) => request.url.endsWith("/release")).length, 0);
    g.node.onRemoved();
    await flush();
    assert.equal(g.windowEvents.has("pagehide"), false);
});

test("removal cancels queued mutations while releasing the active history", async () => {
    const g = await gallery();
    const first = g.run(1, 1);
    const second = g.run(1, 2);
    g.emit(first);
    g.emit(second);
    g.currentHistory().runs = [first, second];
    g.server.delayReads = true;
    const deleting = g.node._generationHistory.changeHistory("/generation-history/delete-run", { run_id: 1 });
    await flush();
    const clearing = g.node._generationHistory.changeHistory("/generation-history/clear", {}, true);
    g.node.onRemoved();
    g.server.delayReads = false;
    g.server.delayedReads.shift()();
    await Promise.all([deleting, clearing]);
    await flush();
    assert.equal(g.requests.filter((request) => request.url.endsWith("/clear")).length, 0);
    assert.equal(g.currentHistory().runs.length, 0);
});
