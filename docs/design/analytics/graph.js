/* HUD.graph: interaction node graph. REFERENCE IMPLEMENTATION for AN-4.
 *
 * Coder: port this into static/js/hud.js as HUD.graph (keep the API), and
 * vendor d3-force into static/vendor/ (see UX_SPEC.md §2.3). Dependencies:
 * d3-force 3 (+ d3-dispatch, d3-quadtree, d3-timer), 17.9 KB min / 6.3 KB gz.
 * No d3-selection: the DOM is built here by hand, so the bundle stays small.
 *
 *   HUD.graph(el, {nodes, edges, engines, commander, onSelect})
 *     nodes   [{id, degree, engine_ids[]}]       id = card name
 *     edges   [{source, target, reasons[]}]       directed: source output feeds target input
 *     engines [{id, name, cards[]}]
 *   returns {focusEngine(id|null), select(id|null), search(q)}
 *
 * Layout runs SYNCHRONOUSLY (300 ticks) before first paint, so it never
 * jitters and reduced-motion users get the same static picture. The only
 * motion is a staggered node pop-in (CSS, disabled under reduced motion).
 */
(function () {
    "use strict";
    var NS = "http://www.w3.org/2000/svg";
    function el(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
    function css(name, fallback) { var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)); return isNaN(v) ? fallback : v; }

    function graph(svg, opt) {
        var W = svg.clientWidth || 800, H = svg.clientHeight || 560;
        svg.setAttribute("viewBox", "0 0 " + W + " " + H);
        var rMin = css("--graph-node-min", 5), rMax = css("--graph-node-max", 18);
        var maxDeg = Math.max.apply(null, opt.nodes.map(function (n) { return n.degree; }).concat([1]));
        var radius = function (n) { return n.id === opt.commander ? rMax + 4 : rMin + (rMax - rMin) * Math.sqrt(n.degree / maxDeg); };

        var nodes = opt.nodes.map(function (n) { return Object.assign({}, n); });
        var byId = {}; nodes.forEach(function (n) { byId[n.id] = n; });
        var links = opt.edges.filter(function (e) { return byId[e.source] && byId[e.target]; })
            .map(function (e) { return { source: e.source, target: e.target, reasons: e.reasons }; });
        var engCards = {}; (opt.engines || []).forEach(function (g) { engCards[g.id] = g.cards; });

        var cmd = byId[opt.commander]; if (cmd) { cmd.fx = W / 2; cmd.fy = H / 2; }
        var sim = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(function (l) { return 70 + 18 / l.reasons.length; }).strength(0.35))
            .force("charge", d3.forceManyBody().strength(-340))
            .force("collide", d3.forceCollide().radius(function (d) { return radius(d) + 26; }).iterations(2))
            .force("x", d3.forceX(W / 2).strength(0.06)).force("y", d3.forceY(H / 2).strength(0.09))
            .stop();
        for (var i = 0; i < 300; i++) sim.tick();
        // Fit the viewBox to the laid-out bounds (+ label room) so nothing clips
        // and the cluster is centred whatever the aspect ratio.
        var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        nodes.forEach(function (n) { var r = radius(n); x0 = Math.min(x0, n.x - r - 60); x1 = Math.max(x1, n.x + r + 60); y0 = Math.min(y0, n.y - r - 12); y1 = Math.max(y1, n.y + r + 22); });
        var full = [x0, y0, x1 - x0, y1 - y0];
        function setView(b) { svg.setAttribute("viewBox", b.map(function (v) { return v.toFixed(0); }).join(" ")); }
        setView(full);
        svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
        // Zoom-to-fit a node set (engine focus). Jumps; no animated viewBox
        // tween (that would re-layout text every frame). The opacity fade on
        // non-members carries the motion.
        function fit(set) {
            if (!set) { setView(full); return; }
            var a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
            nodes.forEach(function (n) { if (!set.has(n.id)) return; var r = radius(n); a = Math.min(a, n.x - r - 90); c = Math.max(c, n.x + r + 90); b = Math.min(b, n.y - r - 24); d = Math.max(d, n.y + r + 30); });
            var w = Math.max(c - a, full[2] * 0.45), h = Math.max(d - b, full[3] * 0.45), cx = (a + c) / 2, cy = (b + d) / 2;
            setView([cx - w / 2, cy - h / 2, w, h]);
        }

        var gE = el("g", {}), gL = el("g", {}), gN = el("g", {});
        svg.appendChild(gE); svg.appendChild(gN); svg.appendChild(gL);
        // Arrowhead marker (directed edges: output → input)
        var defs = el("defs", {}); var mk = el("marker", { id: "g-arrow", viewBox: "0 0 8 8", refX: "8", refY: "4", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
        var mp = el("path", { d: "M0,0 L8,4 L0,8 z" }); mp.style.fill = "var(--signal)"; mk.appendChild(mp); defs.appendChild(mk); svg.insertBefore(defs, svg.firstChild);

        links.forEach(function (l) {
            var s = l.source, t = l.target, dx = t.x - s.x, dy = t.y - s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
            var rt = radius(t) + 3, x2 = t.x - dx / d * rt, y2 = t.y - dy / d * rt;
            l.line = el("line", { x1: s.x, y1: s.y, x2: x2, y2: y2, class: "g-edge" });
            l.line.style.strokeWidth = String(0.75 + l.reasons.length * 0.75);
            gE.appendChild(l.line);
            l.label = el("text", { x: (s.x + t.x) / 2, y: (s.y + t.y) / 2 - 3, "text-anchor": "middle", class: "g-elabel" });
            l.label.textContent = l.reasons.join(" · ");
            gL.appendChild(l.label);
        });

        var order = nodes.slice().sort(function (a, b) { return b.degree - a.degree; });
        order.forEach(function (n, idx) {
            // Outer <g> owns the SVG position; the inner <g> owns the CSS
            // entrance animation. (A CSS transform on the outer <g> would
            // REPLACE its translate() attribute and stack every node at 0,0.)
            var g = el("g", { class: "g-node" + (n.id === opt.commander ? " is-commander" : "") + (n.degree < 3 && n.id !== opt.commander ? " is-label-off" : ""),
                transform: "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")", tabindex: "0", role: "button",
                "aria-label": n.id + ", " + n.degree + " connections" });
            var inner = el("g", { class: "g-node-in is-entering" });
            inner.style.setProperty("--i", idx);
            inner.appendChild(el("circle", { r: radius(n).toFixed(1) }));
            var tx = el("text", { y: (radius(n) + 13).toFixed(1), "text-anchor": "middle" }); tx.textContent = n.id.split(",")[0];
            inner.appendChild(tx);
            g.appendChild(inner);
            g.addEventListener("mouseenter", function () { if (!state.engine && !state.sel) hover(n.id); });
            g.addEventListener("mouseleave", function () { if (!state.engine && !state.sel) clear(); });
            g.addEventListener("focus", function () { if (!state.engine) hover(n.id); });
            g.addEventListener("blur", function () { if (!state.engine && !state.sel) clear(); });
            g.addEventListener("click", function () { select(n.id); });
            g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n.id); } });
            n.g = g; gN.appendChild(g);
        });
        svg.addEventListener("click", function (e) { if (e.target === svg) { select(null); focusEngine(null); } });

        var state = { engine: null, sel: null };
        function paint(set, hotEdges, showLabels) {
            svg.classList.toggle("is-focusing", !!set);
            nodes.forEach(function (n) { n.g.classList.toggle("is-near", !!set && set.has(n.id)); n.g.classList.toggle("is-engine", !!state.engine && set && set.has(n.id)); });
            // Label de-dupe: hub spokes share reasons ("cheat into play" x30).
            // Label the first edge of each (hub, reasons) group with "×N" and
            // leave the rest unlabelled, so the picture stays readable.
            var groups = {};
            links.forEach(function (l) {
                var on = !!set && hotEdges(l);
                l.line.classList.toggle("is-near", on); l.line.classList.toggle("is-hot", on);
                l.line.setAttribute("marker-end", on ? "url(#g-arrow)" : "");
                l.label.classList.remove("is-on");
                if (!on || !showLabels) return;
                var hub = (byId[l.source.id].degree >= byId[l.target.id].degree) ? l.source.id : l.target.id;
                var key = hub + "|" + l.reasons.join("·");
                (groups[key] = groups[key] || []).push(l);
            });
            Object.keys(groups).forEach(function (k) {
                var g = groups[k], l = g[Math.floor(g.length / 2)];
                l.label.textContent = l.reasons.join(" · ") + (g.length > 1 ? " ×" + g.length : "");
                l.label.classList.add("is-on");
            });
            declutter();
        }
        // Greedy collision pass: keep the label of the heaviest edge, hide any
        // later label whose box hits a kept label or a visible node. Hidden
        // reasons are still listed in the inspector, so nothing is lost.
        function declutter() {
            var kept = [], hit = function (a, b) { return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height; };
            nodes.forEach(function (n) { if (!svg.classList.contains("is-focusing") || n.g.classList.contains("is-near")) { var r = radius(n); kept.push({ x: n.x - r, y: n.y - r, width: 2 * r, height: 2 * r }); } });
            links.filter(function (l) { return l.label.classList.contains("is-on"); })
                .sort(function (a, b) { return b.reasons.length - a.reasons.length; })
                .forEach(function (l) {
                    var bb = l.label.getBBox(), box = { x: bb.x - 2, y: bb.y - 1, width: bb.width + 4, height: bb.height + 2 };
                    if (kept.some(function (k) { return hit(box, k); })) l.label.classList.remove("is-on"); else kept.push(box);
                });
        }
        function neighbours(id) {
            var s = new Set([id]); links.forEach(function (l) { if (l.source.id === id) s.add(l.target.id); if (l.target.id === id) s.add(l.source.id); }); return s;
        }
        function hover(id) { paint(neighbours(id), function (l) { return l.source.id === id || l.target.id === id; }, true); }
        function clear() { paint(null); nodes.forEach(function (n) { n.g.classList.remove("is-engine", "is-selected"); }); }
        function focusEngine(id) {
            state.engine = id; state.sel = null;
            nodes.forEach(function (n) { n.g.classList.remove("is-selected"); });
            if (!id) { clear(); fit(null); return; }
            var set = new Set(engCards[id] || []);
            paint(set, function (l) { return set.has(l.source.id) && set.has(l.target.id); }, true);
            fit(set);
        }
        function select(id) {
            state.sel = id;
            nodes.forEach(function (n) { n.g.classList.toggle("is-selected", n.id === id); });
            if (id && !state.engine) hover(id);
            if (!id && !state.engine) clear();
            if (opt.onSelect) opt.onSelect(id, links.filter(function (l) { return l.source.id === id || l.target.id === id; })
                .map(function (l) { return { out: l.source.id === id, other: l.source.id === id ? l.target.id : l.source.id, reasons: l.reasons }; }));
        }
        function search(q) {
            q = (q || "").trim().toLowerCase();
            if (!q) { if (!state.engine) clear(); return null; }
            var hit = order.find(function (n) { return n.id.toLowerCase().indexOf(q) !== -1; });
            if (hit) { hover(hit.id); return hit.id; }
            return null;
        }
        return { focusEngine: focusEngine, select: select, search: search, nodeEl: function (id) { return byId[id] && byId[id].g; } };
    }
    window.HUD = window.HUD || {};
    window.HUD.graph = graph;
})();
