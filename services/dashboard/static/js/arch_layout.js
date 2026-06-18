// ================================================================
// arch_layout.js — 재사용 가능한 DAG 레이아웃 + SVG 유틸리티
// arch_topo.js에서 분리. 의존성 없음 (순수 함수).
// ================================================================

function _svgE(t, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', t); if (a) for (var k in a) e.setAttribute(k, a[k]); return e; }

// BFS longest-path + barycenter crossing minimization + group clustering
function _archLayout(nodes, edges, W, H) {
    var XGAP = 160, YGAP = 100, PAD = 80;
    var nameSet = {}; nodes.forEach(function(n) { nameSet[n.name] = n; });

    var edgePairSeen = {};
    var dagEdges = [];
    edges.forEach(function(e) {
        if (!nameSet[e.source] || !nameSet[e.target]) return;
        var fwd = e.source + '→' + e.target;
        var rev = e.target + '→' + e.source;
        if (edgePairSeen[rev]) return;
        if (!edgePairSeen[fwd]) { edgePairSeen[fwd] = true; dagEdges.push(e); }
    });

    var adj = {}, radj = {}, inD = {}, outD = {};
    nodes.forEach(function(n) { adj[n.name] = []; radj[n.name] = []; inD[n.name] = 0; outD[n.name] = 0; });
    dagEdges.forEach(function(e) {
        adj[e.source].push(e.target); radj[e.target].push(e.source);
        outD[e.source] = (outD[e.source] || 0) + 1; inD[e.target] = (inD[e.target] || 0) + 1;
    });

    var layer = {}, maxL = 0;
    var visited = {};
    var extNames = {};
    nodes.forEach(function(n) {
        if (n.namespace === 'external' || n.kind === 'ExternalService') extNames[n.name] = true;
    });

    function bfsPass(startNodes) {
        startNodes.forEach(function(r) {
            if (layer[r] === undefined) layer[r] = 0;
            visited[r] = true;
        });
        var queue = startNodes.slice();
        var cap = nodes.length * nodes.length + 1, iter = 0;
        while (queue.length && iter < cap) {
            var cur = queue.shift(); iter++;
            (adj[cur] || []).forEach(function(nb) {
                var nl = layer[cur] + 1;
                if (layer[nb] === undefined || nl > layer[nb]) { layer[nb] = nl; if (nl > maxL) maxL = nl; }
                if (!visited[nb]) { visited[nb] = true; queue.push(nb); }
            });
        }
    }

    var roots = [];
    nodes.forEach(function(n) {
        if (!extNames[n.name] && inD[n.name] === 0) roots.push(n.name);
    });
    if (roots.length) bfsPass(roots);

    var changed = true;
    while (changed) {
        changed = false;
        var best = null, bestOut = -1;
        nodes.forEach(function(n) {
            if (!visited[n.name] && !extNames[n.name]) {
                if (outD[n.name] > bestOut) { bestOut = outD[n.name]; best = n.name; }
            }
        });
        if (best) { bfsPass([best]); changed = true; }
    }

    nodes.forEach(function(n) {
        if (extNames[n.name]) layer[n.name] = maxL + 1;
        else if (layer[n.name] === undefined) layer[n.name] = 0;
    });
    maxL = 0; nodes.forEach(function(n) { if (layer[n.name] > maxL) maxL = layer[n.name]; });

    var layers = [];
    for (var i = 0; i <= maxL; i++) layers.push([]);
    nodes.forEach(function(n) { layers[layer[n.name]].push(n.name); });

    function orderIdx(arr) { var m = {}; arr.forEach(function(n, i) { m[n] = i; }); return m; }
    function barySort(layerArr, refArr, adjMap) {
        var refIdx = orderIdx(refArr);
        layerArr.sort(function(a, b) {
            var na = adjMap[a] || [], nb = adjMap[b] || [];
            var ba = 0, bb = 0, ca = 0, cb = 0;
            na.forEach(function(x) { if (refIdx[x] !== undefined) { ba += refIdx[x]; ca++; } });
            nb.forEach(function(x) { if (refIdx[x] !== undefined) { bb += refIdx[x]; cb++; } });
            ba = ca ? ba / ca : 999; bb = cb ? bb / cb : 999;
            return ba - bb;
        });
    }
    for (var i = 1; i <= maxL; i++) barySort(layers[i], layers[i - 1], radj);
    for (var i = maxL - 1; i >= 0; i--) barySort(layers[i], layers[i + 1], adj);

    layers.forEach(function(col) {
        col.sort(function(a, b) {
            var ga = (nameSet[a] || {}).group || '';
            var gb = (nameSet[b] || {}).group || '';
            if (ga !== gb) return ga < gb ? -1 : 1;
            return 0;
        });
    });

    var numLayers = maxL + 1;
    var usableW = Math.max(W - PAD * 2, numLayers * XGAP);
    var xStep = numLayers > 1 ? usableW / (numLayers - 1) : 0;
    var pos = {};
    layers.forEach(function(col, li) {
        var x = PAD + li * xStep;
        var totalH = col.length * YGAP;
        var startY = Math.max(PAD, (H - totalH) / 2);
        col.forEach(function(name, ni) {
            pos[name] = {x: x, y: startY + ni * YGAP};
        });
    });
    return pos;
}
