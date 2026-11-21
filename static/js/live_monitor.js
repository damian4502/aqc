/* Live monitor: MQTT values, formulas, and a scrolling chart.

   The chart appends one point per frame so the line keeps moving. Smoothing
   eases the drawn value toward the latest sample. Raw mode draws that sample
   directly. Several parameters at once are drawn as z-scores.
*/
(function () {
    const KEY = "aqc-live-monitor-v1";
    const MAX_POINTS = 720;
    const PALETTE = ["#38bdf8", "#34d399", "#f59e0b", "#f472b6", "#a78bfa", "#f87171", "#22d3ee", "#84cc16", "#fb7185", "#818cf8"];

    const catalogEl = document.getElementById("monitor-catalog");
    if (!catalogEl || typeof Chart === "undefined") return;

    const catalog = JSON.parse(catalogEl.textContent || "{}");
    const paramById = new Map((catalog.parameters || []).map(function (param) { return [param.id, param]; }));
    const roomById = new Map((catalog.rooms || []).map(function (room) { return [room.id, room]; }));
    const identToId = {};
    (catalog.parameters || []).forEach(function (param) {
        if (param.identifier) identToId[param.identifier] = param.id;
    });
    const topics = {};
    (catalog.topics || []).forEach(function (row) { topics[row.topic] = row; });

    const state = { paramIds: [], roomIds: [], entries: [], smooth: true };
    const latest = {};
    const seriesByKey = new Map();
    let chart = null;
    let ownedTopics = new Set();
    let lastSaved = "";
    let applyingRemote = false;
    let lastScale = "";

    function isRoomScoped(param) {
        return param && !param.global;
    }

    function checkedParams() {
        return state.paramIds.map(function (id) { return paramById.get(id); }).filter(Boolean);
    }

    function distinctParamCount() {
        const ids = new Set();
        state.entries.forEach(function (entry) { ids.add(entry.paramId); });
        return ids.size;
    }

    function zscoreOn() {
        return distinctParamCount() > 1;
    }

    function seriesColor(room, param) {
        const roomColor = (room && room.color) || "";
        const paramColor = (param && param.color) || "";
        if (distinctParamCount() > 1) return paramColor || roomColor || "";
        return roomColor || paramColor || "";
    }

    function formatValue(value, decimals) {
        if (!Number.isFinite(value)) return "";
        const places = Math.max(0, Math.min(4, decimals || 0));
        return value.toFixed(places);
    }

    function valueOf(roomId, paramId, stack) {
        const param = paramById.get(paramId);
        const key = String(roomId == null ? "g" : roomId) + ":" + paramId;
        if (stack.has(key)) return null;
        if (param && param.ast) {
            stack.add(key);
            const boundRoom = param.roomBound ? roomId : null;
            const value = evalNode(param.ast, boundRoom, stack);
            stack.delete(key);
            return value;
        }
        if (roomId == null || !latest[roomId]) return null;
        const sample = latest[roomId][paramId];
        return Number.isFinite(sample) ? sample : null;
    }

    function evalNode(node, roomId, stack) {
        if (!node) return null;
        if (node.t === "n") return Number(node.v);
        if (node.t === "x") return valueOf(roomId, node.p, stack);
        if (node.t === "s") {
            const param = paramById.get(node.p);
            if (param && param.ast && param.global) return valueOf(null, node.p, stack);
            return valueOf(node.r, node.p, stack);
        }
        if (node.t === "op") {
            const left = evalNode(node.l, roomId, stack);
            const right = evalNode(node.r, roomId, stack);
            if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
            if (node.o === "+") return left + right;
            if (node.o === "-") return left - right;
            if (node.o === "*") return left * right;
            if (node.o === "/") return right === 0 ? null : left / right;
            return null;
        }
        if (node.t === "fn") {
            const args = (node.a || []).map(function (arg) { return evalNode(arg, roomId, stack); });
            if (!args.length || args.some(function (value) { return !Number.isFinite(value); })) return null;
            if (node.n === "min") return Math.min.apply(null, args);
            if (node.n === "max") return Math.max.apply(null, args);
            if (node.n === "avg") return args.reduce(function (sum, value) { return sum + value; }, 0) / args.length;
        }
        return null;
    }

    function targetFor(entry) {
        const param = paramById.get(entry.paramId);
        if (!param) return null;
        if (param.ast) return evalNode(param.ast, param.roomBound ? entry.roomId : null, new Set());
        return valueOf(entry.roomId, entry.paramId, new Set());
    }

    function noteSample(series, value) {
        if (!Number.isFinite(value) || series.lastSample === value) return;
        series.lastSample = value;
        const alpha = 0.05;
        if (!Number.isFinite(series.zMean)) {
            series.zMean = value;
            series.zVar = 0;
            series.zN = 1;
            return;
        }
        series.zN += 1;
        const diff = value - series.zMean;
        series.zMean += alpha * diff;
        series.zVar = (1 - alpha) * (series.zVar + alpha * diff * diff);
    }

    function zOf(series, value) {
        if (!series.zN || series.zN < 5) return 0;
        const sd = Math.sqrt(series.zVar);
        if (!Number.isFinite(sd) || sd < 1e-9) return 0;
        return (value - series.zMean) / sd;
    }

    function entryKey(entry) {
        return (entry.roomId == null ? "g" : String(entry.roomId)) + ":" + entry.paramId;
    }

    function labelFor(entry) {
        const param = paramById.get(entry.paramId);
        const room = entry.roomId == null ? null : roomById.get(entry.roomId);
        if (param && param.global) return param.name;
        if (room && param) return room.name + " · " + param.name;
        return (room && room.name) || (param && param.name) || "Series";
    }

    function collectRooms(param, into, stack) {
        if (!param || stack.has(param.id)) return;
        stack.add(param.id);
        walk(param.ast);
        stack.delete(param.id);
        function walk(node) {
            if (!node) return;
            if (node.t === "s") into.add(node.r);
            if (node.t === "op") { walk(node.l); walk(node.r); }
            if (node.t === "fn") (node.a || []).forEach(walk);
            if (node.t === "s" || node.t === "x") collectRooms(paramById.get(node.p), into, stack);
        }
    }

    function measuredIdentifiers() {
        const names = [];
        const seen = new Set();
        function walk(param, stack) {
            if (!param || stack.has(param.id)) return;
            if (!param.ast) {
                if (param.identifier && !seen.has(param.identifier)) {
                    seen.add(param.identifier);
                    names.push(param.identifier);
                }
                return;
            }
            stack.add(param.id);
            walkNode(param.ast, stack);
            stack.delete(param.id);
        }
        function walkNode(node, stack) {
            if (!node) return;
            if (node.t === "op") { walkNode(node.l, stack); walkNode(node.r, stack); }
            if (node.t === "fn") (node.a || []).forEach(function (arg) { walkNode(arg, stack); });
            if (node.t === "s" || node.t === "x") walk(paramById.get(node.p), stack);
        }
        checkedParams().forEach(function (param) { walk(param, new Set()); });
        return names;
    }

    function sensorIdent(topic) {
        const parts = String(topic || "").split("/");
        return parts.length > 2 ? parts[2] : "";
    }

    function syncSubscriptions() {
        if (typeof client === "undefined" || !client) return;
        const rooms = new Set(state.roomIds);
        checkedParams().forEach(function (param) { collectRooms(param, rooms, new Set()); });
        const wanted = new Set();
        Object.keys(topics).forEach(function (topic) {
            if (rooms.has(topics[topic].roomId)) wanted.add(topic);
        });
        ownedTopics.forEach(function (topic) {
            if (!wanted.has(topic)) {
                try { client.unsubscribe(topic); } catch (err) { /* already gone */ }
            }
        });
        wanted.forEach(function (topic) {
            if (!ownedTopics.has(topic)) {
                try { client.subscribe(topic, { qos: 0 }); } catch (err) { /* reconnect will retry */ }
            }
        });
        ownedTopics = wanted;
    }

    function publishLive() {
        if (typeof pageVisible !== "undefined" && !pageVisible) return;
        if (typeof client === "undefined" || !client) return;
        const names = measuredIdentifiers();
        if (!names.length) return;
        ownedTopics.forEach(function (topic) {
            const ident = sensorIdent(topic);
            if (!ident) return;
            names.forEach(function (name) {
                client.publish("vila/sentjane/asgard/cmd/" + ident, name, { qos: 0 });
            });
        });
    }

    function paletteColor(index) {
        return PALETTE[index % PALETTE.length];
    }

    function renderChoices() {
        const box = document.getElementById("param-choices");
        if (!box) return;
        box.innerHTML = "";
        (catalog.parameters || []).forEach(function (param) {
            const label = document.createElement("label");
            label.className = "flex items-center gap-2 px-2 py-1 rounded-xl hover:bg-slate-800/80 cursor-pointer text-sm";
            const input = document.createElement("input");
            input.type = "checkbox";
            input.className = "param-pick accent-cyan-500 w-4 h-4";
            input.value = String(param.id);
            input.checked = state.paramIds.indexOf(param.id) !== -1;
            input.addEventListener("change", function () { toggleParam(param.id, input.checked); });
            const text = document.createElement("span");
            text.className = "text-slate-200";
            text.textContent = param.name;
            label.appendChild(input);
            label.appendChild(text);
            if (param.global) {
                const tag = document.createElement("span");
                tag.className = "text-amber-300/80 text-xs";
                tag.textContent = "(izračun)";
                label.appendChild(tag);
            } else if (param.roomBound) {
                const tag = document.createElement("span");
                tag.className = "text-amber-300/80 text-xs";
                tag.textContent = "(po prostorih)";
                label.appendChild(tag);
            }
            if (param.formula && !param.ast) {
                const tag = document.createElement("span");
                tag.className = "text-red-400/80 text-xs";
                tag.textContent = "(napaka formule)";
                label.appendChild(tag);
            }
            if (param.unit) {
                const unit = document.createElement("span");
                unit.className = "text-slate-500 text-xs";
                unit.textContent = param.unit;
                label.appendChild(unit);
            }
            box.appendChild(label);
        });
        const roomSelect = document.getElementById("add-room");
        if (!roomSelect) return;
        const current = roomSelect.value;
        roomSelect.innerHTML = '<option value="">-- Izberi prostor --</option>';
        (catalog.rooms || []).forEach(function (room) {
            const option = document.createElement("option");
            option.value = String(room.id);
            option.textContent = room.name;
            roomSelect.appendChild(option);
        });
        roomSelect.value = current;
    }

    function toggleParam(paramId, on) {
        const param = paramById.get(paramId);
        if (!param) return;
        if (on) {
            if (state.paramIds.indexOf(paramId) === -1) state.paramIds.push(paramId);
            if (param.global) addEntry(null, paramId);
            else state.roomIds.forEach(function (roomId) { addEntry(roomId, paramId); });
        } else {
            state.paramIds = state.paramIds.filter(function (id) { return id !== paramId; });
            state.entries = state.entries.filter(function (entry) { return entry.paramId !== paramId; });
        }
        commit();
    }

    function addEntry(roomId, paramId) {
        const param = paramById.get(paramId);
        if (!param) return;
        if (param.global) roomId = null;
        else if (roomId == null) return;
        const key = entryKey({ roomId: roomId, paramId: paramId });
        if (state.entries.some(function (entry) { return entryKey(entry) === key; })) return;
        state.entries.push({ roomId: roomId, paramId: paramId });
        if (roomId != null && state.roomIds.indexOf(roomId) === -1) state.roomIds.push(roomId);
    }

    function addRoom(roomId) {
        roomId = parseInt(roomId, 10);
        if (!roomId) return;
        if (state.roomIds.indexOf(roomId) === -1) state.roomIds.push(roomId);
        checkedParams().forEach(function (param) {
            if (isRoomScoped(param)) addEntry(roomId, param.id);
        });
        commit();
    }

    function addAllRooms() {
        const rooms = new Set();
        checkedParams().forEach(function (param) {
            if (!isRoomScoped(param)) return;
            if (param.roomBound) {
                (catalog.rooms || []).forEach(function (room) { rooms.add(room.id); });
            } else {
                (param.liveRooms || []).forEach(function (id) { rooms.add(id); });
            }
        });
        rooms.forEach(function (roomId) {
            if (state.roomIds.indexOf(roomId) === -1) state.roomIds.push(roomId);
            checkedParams().forEach(function (param) {
                if (isRoomScoped(param)) addEntry(roomId, param.id);
            });
        });
        commit();
    }

    function removeEntry(key) {
        const before = state.entries.length;
        state.entries = state.entries.filter(function (entry) { return entryKey(entry) !== key; });
        const still = new Set(state.entries.map(function (entry) { return entry.roomId; }));
        state.roomIds = state.roomIds.filter(function (id) { return still.has(id); });
        if (state.entries.length !== before) commit();
    }

    function removeAllRooms() {
        if (!window.confirm("Res želiš odstraniti vse serije iz tabele in grafa?")) return;
        state.entries = [];
        state.roomIds = [];
        clearChart();
        commit();
    }

    function save() {
        const payload = JSON.stringify({
            paramIds: state.paramIds,
            roomIds: state.roomIds,
            entries: state.entries,
            smooth: state.smooth,
        });
        if (payload === lastSaved) return;
        lastSaved = payload;
        try { localStorage.setItem(KEY, payload); } catch (err) { /* private mode */ }
    }

    function applyStored(raw) {
        let data = {};
        try { data = JSON.parse(raw || "{}"); } catch (err) { data = {}; }
        applyingRemote = true;
        state.paramIds = (data.paramIds || []).map(Number).filter(function (id) { return paramById.has(id); });
        state.roomIds = (data.roomIds || []).map(Number).filter(function (id) { return roomById.has(id); });
        state.entries = (data.entries || []).filter(function (entry) {
            return paramById.has(entry.paramId) && (entry.roomId == null || roomById.has(entry.roomId));
        }).map(function (entry) {
            return { roomId: entry.roomId == null ? null : Number(entry.roomId), paramId: Number(entry.paramId) };
        });
        state.smooth = data.smooth !== false;
        const smoothBox = document.getElementById("smooth-curve");
        if (smoothBox) smoothBox.checked = state.smooth;
        lastSaved = JSON.stringify({
            paramIds: state.paramIds,
            roomIds: state.roomIds,
            entries: state.entries,
            smooth: state.smooth,
        });
        applyingRemote = false;
        renderChoices();
        rebuild();
        if (!applyingRemote) publishLive();
    }

    function commit() {
        renderChoices();
        rebuild();
        if (!applyingRemote) save();
        publishLive();
    }

    function rebuild() {
        const table = document.getElementById("delta-table");
        const keep = new Map(seriesByKey);
        seriesByKey.clear();
        const zscore = zscoreOn();
        state.entries.forEach(function (entry, index) {
            const key = entryKey(entry);
            const param = paramById.get(entry.paramId);
            const room = entry.roomId == null ? null : roomById.get(entry.roomId);
            const previous = keep.get(key);
            const series = previous || {
                key: key,
                prev: null,
                ema: 0,
                zMean: null,
                zVar: 0,
                zN: 0,
                lastSample: null,
                latest: null,
                display: null,
            };
            series.entry = entry;
            series.label = labelFor(entry);
            series.color = seriesColor(room, param) || paletteColor(index);
            series.unit = (param && param.unit) || "";
            series.decimals = param ? param.decimals : 2;
            seriesByKey.set(key, series);
        });
        if (table) {
            table.innerHTML = "";
            seriesByKey.forEach(function (series) { table.appendChild(renderRow(series)); });
        }
        syncChartDatasets();
        syncSubscriptions();
        updateScaleBadge(zscore);
        updateStats();
    }

    function renderRow(series) {
        const row = document.createElement("tr");
        row.dataset.key = series.key;
        const roomId = series.entry.roomId;
        if (roomId != null) {
            Object.keys(topics).forEach(function (topic) {
                if (topics[topic].roomId === roomId && !row.dataset.sensorIdentifier) {
                    row.dataset.sensorIdentifier = sensorIdent(topic);
                }
            });
        }
        row.innerHTML = ""
            + '<td class="py-3 px-3 font-medium">'
            + '<div class="flex items-center gap-2">'
            + '<span class="inline-block w-2.5 h-2.5 rounded-full shrink-0" style="background:' + series.color + '"></span>'
            + "<span>" + escapeHtml(series.label) + "</span>"
            + '<button type="button" class="series-remove ml-auto text-red-400 hover:text-red-500 text-xl leading-none" aria-label="Remove">✕</button>'
            + "</div></td>"
            + '<td class="text-right py-3 px-3 font-mono tabular-nums series-value"></td>';
        row.querySelector(".series-remove").addEventListener("click", function () { removeEntry(series.key); });
        return row;
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, function (ch) {
            var amp = String.fromCharCode(38);
            if (ch === "&") return amp + "amp;";
            if (ch === "<") return amp + "lt;";
            if (ch === ">") return amp + "gt;";
            if (ch === '"') return amp + "quot;";
            return amp + "#39;";
        });
    }

    function syncChartDatasets() {
        if (!chart) return;
        const length = chart.data.labels.length;
        const next = [];
        seriesByKey.forEach(function (series) {
            const existing = chart.data.datasets.find(function (ds) { return ds.key === series.key; });
            const data = existing ? existing.data.slice() : new Array(length).fill(null);
            while (data.length < length) data.unshift(null);
            if (data.length > length) data.splice(0, data.length - length);
            next.push({
                key: series.key,
                label: series.label,
                data: data,
                borderColor: series.color,
                backgroundColor: series.color,
                tension: state.smooth ? 0.35 : 0,
                borderWidth: 2.5,
                pointRadius: 0,
                fill: false,
                spanGaps: true,
            });
        });
        chart.data.datasets = next;
        safeUpdate();
    }

    function clearChart() {
        seriesByKey.forEach(function (series) {
            series.prev = null;
            series.ema = 0;
        });
        if (!chart) return;
        chart.data.labels = [];
        chart.data.datasets.forEach(function (ds) { ds.data = []; });
        safeUpdate();
    }

    function safeUpdate() {
        if (!chart) return;
        try {
            chart.update("none");
        } catch (err) {
            const canvas = document.getElementById("deltaChart");
            if (!canvas) return;
            const labels = chart.data.labels.slice();
            const datasets = chart.data.datasets.map(function (ds) {
                return Object.assign({}, ds, { data: ds.data.slice() });
            });
            chart.destroy();
            chart = null;
            initChart();
            chart.data.labels = labels;
            chart.data.datasets = datasets;
            try { chart.update("none"); } catch (again) { /* keep the fresh chart */ }
        }
    }

    function initChart() {
        const canvas = document.getElementById("deltaChart");
        if (!canvas) return;
        chart = new Chart(canvas.getContext("2d"), {
            type: "line",
            data: { labels: [], datasets: [] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                spanGaps: true,
                interaction: { mode: "nearest", intersect: false },
                scales: {
                    y: {
                        beginAtZero: false,
                        title: { display: true, text: "Vrednost", color: "#94a3b8" },
                        grid: { color: "rgba(148,163,184,0.15)" },
                        ticks: { color: "#94a3b8", maxTicksLimit: 8 },
                    },
                    x: {
                        grid: { color: "rgba(148,163,184,0.15)" },
                        ticks: { color: "#94a3b8", maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
                    },
                },
                plugins: {
                    legend: {
                        display: true,
                        position: "top",
                        labels: { color: "#e2e8f0", boxWidth: 12, padding: 12 },
                    },
                },
            },
        });
    }

    function updateScaleBadge(zscore) {
        const badge = document.getElementById("scale-badge");
        if (badge) badge.textContent = zscore ? "z-score" : "vrednost";
        const note = document.getElementById("stat-source");
        if (note) note.textContent = zscore ? "z-score na grafu" : "iz trenutnih vrednosti";
        if (chart && lastScale !== (zscore ? "z" : "v")) {
            if (lastScale) clearChart();
            lastScale = zscore ? "z" : "v";
            chart.options.scales.y.title.text = zscore ? "z-score" : "Vrednost";
        }
    }

    function paintCell(series, raw) {
        const row = document.querySelector('#delta-table tr[data-key="' + series.key + '"] .series-value');
        if (!row) return;
        const text = formatValue(raw, series.decimals);
        if (row.textContent === text) return;
        row.textContent = text;
        row.classList.add("!text-emerald-400");
        window.setTimeout(function () { row.classList.remove("!text-emerald-400"); }, 280);
    }

    function chartIsShown() {
        return !document.body.classList.contains("monitor-pane-table");
    }

    function paintLatest() {
        seriesByKey.forEach(function (series) {
            const target = targetFor(series.entry);
            if (!Number.isFinite(target)) return;
            series.latest = target;
            paintCell(series, target);
        });
        updateStats();
    }

    function tick() {
        if (!chartIsShown() || !chart || !chart.data.datasets.length) {
            paintLatest();
            return;
        }
        const zscore = zscoreOn();
        updateScaleBadge(zscore);
        const time = new Date().toLocaleTimeString("sl-SI", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        chart.data.labels.push(time);
        if (chart.data.labels.length > MAX_POINTS) chart.data.labels.shift();
        chart.data.datasets.forEach(function (dataset) {
            const series = seriesByKey.get(dataset.key);
            if (!series) {
                dataset.data.push(null);
            } else {
                const target = targetFor(series.entry);
                let shown = target;
                if (state.smooth && Number.isFinite(series.prev) && Number.isFinite(target)) {
                    const delta = target - series.prev;
                    series.ema = (((Number.isFinite(series.ema) ? series.ema : 0) * 100) + delta) / 101;
                    shown = series.prev + (series.ema / 51);
                }
                if (Number.isFinite(shown)) series.prev = shown;
                let y = shown;
                if (zscore && Number.isFinite(shown)) {
                    noteSample(series, Number.isFinite(target) ? target : shown);
                    y = zOf(series, shown);
                }
                series.display = Number.isFinite(y) ? y : null;
                dataset.data.push(series.display);
                dataset.tension = state.smooth ? 0.35 : 0;
                dataset.borderColor = series.color;
                if (Number.isFinite(target)) {
                    series.latest = target;
                    paintCell(series, target);
                }
            }
            if (dataset.data.length > MAX_POINTS) dataset.data.shift();
        });
        safeUpdate();
        updateStats();
    }

    function updateStats() {
        const minEl = document.getElementById("stat-min");
        const maxEl = document.getElementById("stat-max");
        const rangeEl = document.getElementById("stat-range");
        if (!minEl || !maxEl || !rangeEl) return;
        const zscore = zscoreOn();
        const values = [];
        seriesByKey.forEach(function (series) {
            const value = zscore ? series.display : series.latest;
            if (Number.isFinite(value)) values.push(value);
        });
        if (!values.length) {
            minEl.textContent = "--";
            maxEl.textContent = "--";
            rangeEl.textContent = "--";
            return;
        }
        const minVal = Math.min.apply(null, values);
        const maxVal = Math.max.apply(null, values);
        minEl.textContent = minVal.toFixed(2);
        maxEl.textContent = maxVal.toFixed(2);
        rangeEl.textContent = (maxVal - minVal).toFixed(2);
    }

    function toggleFullscreen() {
        document.body.classList.toggle("monitor-chart-full");
        window.setTimeout(function () { if (chart) chart.resize(); }, 60);
    }

    function openPane(pane) {
        const url = window.location.pathname + "?pane=" + pane;
        window.open(url, "aqc-monitor-" + pane, "popup=yes,width=1200,height=800,resizable=yes");
    }

    function onMessage(topic, message) {
        const meta = topics[topic];
        if (!meta) return;
        let payload;
        try { payload = JSON.parse(message.toString()); } catch (err) { return; }
        if (!payload || typeof payload !== "object") return;
        Object.keys(payload).forEach(function (key) {
            const paramId = identToId[key];
            if (!paramId) return;
            const value = typeof payload[key] === "number" ? payload[key] : parseFloat(payload[key]);
            if (!Number.isFinite(value)) return;
            if (!latest[meta.roomId]) latest[meta.roomId] = {};
            latest[meta.roomId][paramId] = value;
        });
    }

    function bindUi() {
        const addRoomSelect = document.getElementById("add-room");
        if (addRoomSelect) {
            addRoomSelect.addEventListener("change", function () {
                if (!addRoomSelect.value) return;
                addRoom(addRoomSelect.value);
                addRoomSelect.value = "";
            });
        }
        const addAll = document.getElementById("add-all-rooms");
        if (addAll) addAll.addEventListener("click", addAllRooms);
        const removeAll = document.getElementById("remove-all-rooms");
        if (removeAll) removeAll.addEventListener("click", removeAllRooms);
        const clear = document.getElementById("clear-chart");
        const clearSetup = document.getElementById("clear-chart-setup");
        if (clear) clear.addEventListener("click", clearChart);
        if (clearSetup) clearSetup.addEventListener("click", clearChart);
        const full = document.getElementById("full-chart");
        if (full) full.addEventListener("click", toggleFullscreen);
        const exitFull = document.getElementById("exit-full");
        if (exitFull) exitFull.addEventListener("click", toggleFullscreen);
        const openChart = document.getElementById("open-chart-window");
        if (openChart) openChart.addEventListener("click", function () { openPane("chart"); });
        const openTable = document.getElementById("open-table-window");
        if (openTable) openTable.addEventListener("click", function () { openPane("table"); });
        const smooth = document.getElementById("smooth-curve");
        if (smooth) {
            smooth.checked = state.smooth;
            smooth.addEventListener("change", function () {
                state.smooth = smooth.checked;
                syncChartDatasets();
                save();
            });
        }
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && document.body.classList.contains("monitor-chart-full")) {
                toggleFullscreen();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initChart();
        bindUi();
        applyStored(localStorage.getItem(KEY) || "");
        if (typeof client !== "undefined" && client) client.on("message", onMessage);
        window.setInterval(tick, 41);
        window.setInterval(publishLive, 9000);
        publishLive();
    });

    window.addEventListener("storage", function (event) {
        if (event.key !== KEY || !event.newValue || event.newValue === lastSaved) return;
        applyStored(event.newValue);
    });

    window.clearChart = clearChart;
    window.removeAllRooms = removeAllRooms;
    window.addAllRooms = addAllRooms;
})();
