/* Live monitor: MQTT values, formulas, and a scrolling chart.

   The chart appends one point per frame so the line keeps moving. Smoothing
   eases the drawn value toward the latest sample. Raw mode draws that sample
   directly. Several parameters at once are drawn as z-scores.
*/
(function () {
    const KEY = "aqc-live-monitor-v1";
    const MAX_POINTS = 720;

    const catalogEl = document.getElementById("monitor-catalog");
    if (!catalogEl || typeof Chart === "undefined") return;

    const catalog = JSON.parse(catalogEl.textContent || "{}");
    const PALETTE = Array.isArray(catalog.palette) && catalog.palette.length
        ? catalog.palette
        : ["#1ec63e", "#f056fc", "#f48218", "#1db1f3", "#fa1a46", "#b3a919", "#6b78fb", "#00e6c8"];
    const paramById = new Map((catalog.parameters || []).map(function (param) { return [param.id, param]; }));
    const roomById = new Map((catalog.rooms || []).map(function (room) { return [room.id, room]; }));
    const identToId = {};
    (catalog.parameters || []).forEach(function (param) {
        if (param.identifier) identToId[param.identifier] = param.id;
    });
    const topics = {};
    (catalog.topics || []).forEach(function (row) { topics[row.topic] = row; });

    const state = { activeParamId: null, entries: [], smooth: true, scale: "auto" };
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

    function paramsInUse() {
        const seen = new Set();
        const params = [];
        state.entries.forEach(function (entry) {
            if (seen.has(entry.paramId)) return;
            seen.add(entry.paramId);
            const param = paramById.get(entry.paramId);
            if (param) params.push(param);
        });
        return params;
    }

    function distinctParamCount() {
        const ids = new Set();
        state.entries.forEach(function (entry) { ids.add(entry.paramId); });
        return ids.size;
    }

    function unitsDiffer() {
        const units = new Set();
        state.entries.forEach(function (entry) {
            const param = paramById.get(entry.paramId);
            units.add(((param && param.unit) || "").trim());
        });
        return units.size > 1;
    }

    function zscoreOn() {
        if (state.scale === "z") return true;
        if (state.scale === "raw") return false;
        return unitsDiffer();
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

    function psychrometric(name, tempC, rh) {
        if (!Number.isFinite(tempC) || !Number.isFinite(rh) || rh < 0 || tempC <= -243.5) return null;
        const esHpa = 6.112 * Math.exp((17.67 * tempC) / (tempC + 243.5));
        if (name === "ah") {
            const tk = tempC + 273.15;
            if (tk <= 0) return null;
            return (2.1674 * esHpa * rh) / tk;
        }
        return esHpa * rh;
    }

    function humidityDeltaPa(temp1, rh1, temp2, rh2, height) {
        const e1 = psychrometric("avp", temp1, rh1);
        const e2 = psychrometric("avp", temp2, rh2);
        const tk1 = temp1 + 273.15;
        const tk2 = temp2 + 273.15;
        if (!Number.isFinite(e1) || !Number.isFinite(e2) || !Number.isFinite(height) || tk1 <= 0 || tk2 <= 0) return null;
        const density = (1 / 287.058) - (1 / 461.495);
        return 9.80665 * height * density * (e1 / tk1 - e2 / tk2);
    }

    function dewPoint(tempC, rh) {
        if (!Number.isFinite(tempC) || !Number.isFinite(rh) || rh <= 0 || tempC <= -243.5) return null;
        const esHpa = 6.112 * Math.exp((17.67 * tempC) / (tempC + 243.5));
        const gamma = Math.log(esHpa * rh / 100 / 6.112);
        const denom = 17.67 - gamma;
        if (!Number.isFinite(gamma) || denom === 0) return null;
        return (243.5 * gamma) / denom;
    }

    function mixingRatio(tempC, rh, pressure) {
        const vapor = psychrometric("avp", tempC, rh);
        if (!Number.isFinite(vapor) || !Number.isFinite(pressure) || pressure < 200 || pressure > 2000000) return null;
        if (pressure >= 2000 && pressure < 20000) return null;
        const total = pressure < 2000 ? pressure * 100 : pressure;
        if (total <= vapor) return null;
        return 1000 * 0.62198 * vapor / (total - vapor);
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
            if (node.n === "range") return Math.max.apply(null, args) - Math.min.apply(null, args);
            if (node.n === "ah" || node.n === "avp") {
                if (args.length !== 2) return null;
                return psychrometric(node.n, args[0], args[1]);
            }
            if (node.n === "hdp") {
                if (args.length !== 5) return null;
                return humidityDeltaPa(args[0], args[1], args[2], args[3], args[4]);
            }
            if (node.n === "dew") {
                if (args.length !== 2) return null;
                return dewPoint(args[0], args[1]);
            }
            if (node.n === "mr") {
                if (args.length !== 3) return null;
                return mixingRatio(args[0], args[1], args[2]);
            }
            if (node.n === "lt") {
                if (args.length !== 2) return null;
                return args[1] > args[0] ? 1 : 0;
            }
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
        paramsInUse().forEach(function (param) { walk(param, new Set()); });
        return names;
    }

    function sensorIdent(topic) {
        const parts = String(topic || "").split("/");
        return parts.length > 2 ? parts[2] : "";
    }

    function syncSubscriptions() {
        if (typeof client === "undefined" || !client) return;
        const rooms = new Set();
        state.entries.forEach(function (entry) {
            if (entry.roomId != null) rooms.add(entry.roomId);
        });
        paramsInUse().forEach(function (param) { collectRooms(param, rooms, new Set()); });
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
        if (!getPageVisibility()) return;
        if (typeof client === "undefined" || !client) return;
		
		client.publish("vila/sentjane/asgard/cmd/all", "live_mode", { qos: 0 });
		
/*         const names = measuredIdentifiers();
        if (!names.length) return;
        ownedTopics.forEach(function (topic) {
            const ident = sensorIdent(topic);
            if (!ident) return;
            names.forEach(function (name) {
                client.publish("vila/sentjane/asgard/cmd/" + ident, "live_mode", { qos: 0 });
            });
        });
 */    }

    function paletteColor(index) {
        return PALETTE[index % PALETTE.length];
    }

    function claimColor(preferred, used, cursor) {
        const color = String(preferred || "").trim().toLowerCase();
        if (color && !used[color]) {
            used[color] = true;
            return color;
        }
        for (let step = 0; step < PALETTE.length; step += 1) {
            const candidate = PALETTE[cursor.i % PALETTE.length].toLowerCase();
            cursor.i += 1;
            if (!used[candidate]) {
                used[candidate] = true;
                return candidate;
            }
        }
        const fallback = PALETTE[cursor.i % PALETTE.length];
        cursor.i += 1;
        return fallback;
    }

    function assignDistinctColors() {
        const used = {};
        const cursor = { i: 0 };
        let index = 0;
        seriesByKey.forEach(function (series) {
            const entry = series.entry;
            const param = paramById.get(entry.paramId);
            const room = entry.roomId == null ? null : roomById.get(entry.roomId);
            series.color = claimColor(seriesColor(room, param) || paletteColor(index), used, cursor);
            index += 1;
        });
    }

    function paramLabel(param) {
        let text = param.name;
        if (param.global) text += " (calculated)";
        else if (param.roomBound) text += " (per room)";
        if (param.formula && !param.ast) text += " (formula error)";
        if (param.unit) text += " · " + param.unit;
        return text;
    }

    function renderChoices() {
        const paramSelect = document.getElementById("add-param");
        if (paramSelect) {
            const current = String(state.activeParamId || "");
            paramSelect.innerHTML = '<option value="">-- Select a parameter --</option>';
            (catalog.parameters || []).forEach(function (param) {
                const option = document.createElement("option");
                option.value = String(param.id);
                option.textContent = paramLabel(param);
                paramSelect.appendChild(option);
            });
            if (current && paramById.has(Number(current))) paramSelect.value = current;
        }
        const roomSelect = document.getElementById("add-room");
        if (!roomSelect) return;
        const currentRoom = roomSelect.value;
        roomSelect.innerHTML = '<option value="">-- Select a room --</option>';
        (catalog.rooms || []).forEach(function (room) {
            const option = document.createElement("option");
            option.value = String(room.id);
            option.textContent = room.name;
            roomSelect.appendChild(option);
        });
        roomSelect.value = currentRoom;
    }

    function selectParam(paramId) {
        paramId = parseInt(paramId, 10);
        state.activeParamId = paramById.has(paramId) ? paramId : null;
        const param = paramById.get(state.activeParamId);
        if (param && param.global) addEntry(null, param.id);
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
    }

    function addRoom(roomId) {
        roomId = parseInt(roomId, 10);
        const param = paramById.get(state.activeParamId);
        if (!roomId || !param || !isRoomScoped(param)) return;
        addEntry(roomId, param.id);
        commit();
    }

    function addAllRooms() {
        const param = paramById.get(state.activeParamId);
        if (!param || !isRoomScoped(param)) return;
        const rooms = param.roomBound
            ? (catalog.rooms || []).map(function (room) { return room.id; })
            : (param.liveRooms || []).slice();
        rooms.forEach(function (roomId) { addEntry(roomId, param.id); });
        commit();
    }

    function addGroupRooms(roomIds) {
        const param = paramById.get(state.activeParamId);
        if (!param || !isRoomScoped(param)) return;
        const allowed = {};
        const source = param.roomBound
            ? (catalog.rooms || []).map(function (room) { return room.id; })
            : (param.liveRooms || []);
        source.forEach(function (roomId) { allowed[Number(roomId)] = true; });
        (roomIds || []).forEach(function (roomId) {
            if (allowed[Number(roomId)]) addEntry(Number(roomId), param.id);
        });
        commit();
    }

    function removeEntry(key) {
        const before = state.entries.length;
        state.entries = state.entries.filter(function (entry) { return entryKey(entry) !== key; });
        if (state.entries.length !== before) commit();
    }

    function removeAllRooms() {
        state.entries = [];
        clearChart();
        commit();
    }

    function save() {
        const payload = JSON.stringify({
            activeParamId: state.activeParamId,
            entries: state.entries,
            smooth: state.smooth,
            scale: state.scale,
        });
        if (payload === lastSaved) return;
        lastSaved = payload;
        try { localStorage.setItem(KEY, payload); } catch (err) { /* private mode */ }
    }

    function applyStored(raw) {
        let data = {};
        try { data = JSON.parse(raw || "{}"); } catch (err) { data = {}; }
        applyingRemote = true;
        state.entries = (data.entries || []).filter(function (entry) {
            return paramById.has(entry.paramId) && (entry.roomId == null || roomById.has(entry.roomId));
        }).map(function (entry) {
            return { roomId: entry.roomId == null ? null : Number(entry.roomId), paramId: Number(entry.paramId) };
        });
        const storedParam = Number(data.activeParamId);
        state.activeParamId = paramById.has(storedParam) ? storedParam : null;
        if (!state.activeParamId && state.entries.length) {
            state.activeParamId = state.entries[state.entries.length - 1].paramId;
        }
        state.smooth = data.smooth !== false;
        state.scale = data.scale === "z" || data.scale === "raw" ? data.scale : "auto";
        const smoothBox = document.getElementById("smooth-curve");
        if (smoothBox) smoothBox.checked = state.smooth;
        const scaleBox = document.getElementById("scale-mode");
        if (scaleBox) scaleBox.value = state.scale;
        lastSaved = JSON.stringify({
            activeParamId: state.activeParamId,
            entries: state.entries,
            smooth: state.smooth,
            scale: state.scale,
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
        state.entries.forEach(function (entry) {
            const key = entryKey(entry);
            const param = paramById.get(entry.paramId);
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
            series.unit = (param && param.unit) || "";
            series.decimals = param ? param.decimals : 2;
            seriesByKey.set(key, series);
        });
        assignDistinctColors();
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
                        title: { display: true, text: "Value", color: "#94a3b8" },
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
        if (badge) badge.textContent = zscore ? "z-score" : "value";
        const note = document.getElementById("stat-source");
        if (note) note.textContent = "from current values";
        if (chart && lastScale !== (zscore ? "z" : "v")) {
            if (lastScale) clearChart();
            lastScale = zscore ? "z" : "v";
            chart.options.scales.y.title.text = zscore ? "z-score" : "Value";
            safeUpdate();
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
        const time = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        //chart.data.labels.push(time);
		chart.data.labels.push('');
        if (chart.data.labels.length > MAX_POINTS) chart.data.labels.shift();
        chart.data.datasets.forEach(function (dataset) {
            const series = seriesByKey.get(dataset.key);
            if (!series) {
                dataset.data.push(null);
            } else {
                const target = targetFor(series.entry);
                let goal = target;
                if (zscore) {
                    if (Number.isFinite(target)) noteSample(series, target);
                    goal = series.zN >= 5 && Number.isFinite(target) ? zOf(series, target) : null;
                }
                let shown = goal;
                if (state.smooth && Number.isFinite(series.prev) && Number.isFinite(goal)) {
                    const delta = goal - series.prev;
                    series.ema = (((Number.isFinite(series.ema) ? series.ema : 0) * 100) + delta) / 101;
                    shown = series.prev + (series.ema / 51);
                }
                if (Number.isFinite(shown)) series.prev = shown;
                series.display = Number.isFinite(shown) ? shown : null;
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
        const host = document.getElementById("stat-groups");
        if (!host) return;
        const byUnit = new Map();
        seriesByKey.forEach(function (series) {
            if (!Number.isFinite(series.latest)) return;
            const unit = ((series.unit || "") + "").trim();
            if (!byUnit.has(unit)) byUnit.set(unit, []);
            byUnit.get(unit).push(series.latest);
        });
        host.replaceChildren();
        if (!byUnit.size) {
            host.appendChild(statGroup("", null, null, false));
            return;
        }
        const units = Array.from(byUnit.keys()).sort(function (a, b) {
            if (!a) return 1;
            if (!b) return -1;
            return a.localeCompare(b);
        });
        const split = units.length > 1;
        units.forEach(function (unit) {
            const values = byUnit.get(unit);
            host.appendChild(statGroup(unit, Math.min.apply(null, values), Math.max.apply(null, values), split));
        });
    }

    function statGroup(unit, minVal, maxVal, showUnit) {
        const row = document.createElement("div");
        row.className = "flex flex-wrap items-baseline gap-x-6 gap-y-1";
        if (showUnit) {
            const name = document.createElement("span");
            name.className = "text-[11px] uppercase tracking-wide text-slate-500";
            name.textContent = unit || "no unit";
            row.appendChild(name);
        }
        [
            ["Lowest", minVal, "text-emerald-400"],
            ["Highest", maxVal, "text-red-400"],
            ["Range", Number.isFinite(minVal) && Number.isFinite(maxVal) ? maxVal - minVal : null, "text-amber-400"],
        ].forEach(function (item) {
            const pair = document.createElement("span");
            pair.className = "inline-flex items-baseline gap-2";
            const label = document.createElement("span");
            label.className = "text-slate-400";
            label.textContent = item[0] + ":";
            const value = document.createElement("span");
            value.className = "font-mono font-semibold text-base tabular-nums " + item[2];
            value.textContent = Number.isFinite(item[1]) ? item[1].toFixed(2) : "--";
            pair.append(label, value);
            row.appendChild(pair);
        });
        return row;
    }

    function toggleFullscreen() {
        document.body.classList.toggle("monitor-chart-full");
        window.setTimeout(function () { if (chart) chart.resize(); }, 60);
    }

    function openPane(pane) {
        const url = window.location.pathname + "?pane=" + pane;
        window.open(url, "aqc-monitor-" + pane, "popup=yes,width=480,height=640,resizable=yes");
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
        const addParamSelect = document.getElementById("add-param");
        if (addParamSelect) {
            addParamSelect.addEventListener("change", function () {
                selectParam(addParamSelect.value);
            });
        }
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
        const groupHost = document.getElementById("room-group-chips");
        if (groupHost) {
            (catalog.groups || []).forEach(function (group) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "px-3 py-1.5 rounded-xl text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700";
                button.textContent = group.name;
                button.addEventListener("click", function () { addGroupRooms(group.roomIds || group.room_ids || []); });
                groupHost.appendChild(button);
            });
        }
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
        const scaleMode = document.getElementById("scale-mode");
        if (scaleMode) {
            scaleMode.value = state.scale;
            scaleMode.addEventListener("change", function () {
                state.scale = scaleMode.value === "z" || scaleMode.value === "raw" ? scaleMode.value : "auto";
                updateScaleBadge(zscoreOn());
                save();
            });
        }
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
        window.setInterval(publishLive, 5000);
        publishLive();
		document.addEventListener('visibilitychange', publishLive);
    });

    window.addEventListener("storage", function (event) {
        if (event.key !== KEY || !event.newValue || event.newValue === lastSaved) return;
        applyStored(event.newValue);
    });

    window.clearChart = clearChart;
    window.removeAllRooms = removeAllRooms;
    window.addAllRooms = addAllRooms;
})();
