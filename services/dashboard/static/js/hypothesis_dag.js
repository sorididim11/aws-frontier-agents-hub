// ================================================================
// HYPOTHESIS DAG — Shared module for Bedrock hypothesis DAG
// Used by app.js (legacy) and scenario_tab.js (overview)
// ================================================================

var HypothesisDag = (function(){

var STATUS_COLORS = {rejected: '#ef4444', confirmed: '#22c55e', partial: '#f59e0b'};
var STATUS_CSS = {rejected: 'dag-node-rejected', confirmed: 'dag-node-confirmed', partial: 'dag-node-partial'};

function _esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function parse(data) {
    var nodes = [];
    var edges = [];
    var hypotheses = data.hypotheses || [];
    var alarm = data.alarm || 'Triage';

    nodes.push({
        id: 'triage',
        label: alarm.length > 30 ? alarm.substring(0, 27) + '...' : alarm,
        sublabel: 'Triage',
        type: 'triage',
        color: '#94a3b8',
        branch_index: 0,
        is_key: false
    });

    hypotheses.forEach(function(hyp, hi) {
        var branchIdx = hi + 1;
        var status = hyp.status || 'partial';
        var color = STATUS_COLORS[status] || '#f59e0b';
        var hypId = 'hyp-' + (hyp.id || hi);

        nodes.push({
            id: hypId,
            label: hyp.label || hyp.title || ('Hypothesis ' + (hi + 1)),
            sublabel: hyp.category || '',
            type: 'hypothesis',
            color: color,
            branch_index: branchIdx,
            is_key: false
        });
        edges.push({from_id: 'triage', to_id: hypId});

        var prevId = hypId;
        (hyp.steps || []).forEach(function(step, si) {
            var stepId = hypId + '-step-' + si;
            var stepLabel = step.action || step.insight || '';
            if (stepLabel.length > 40) stepLabel = stepLabel.substring(0, 37) + '...';
            nodes.push({
                id: stepId,
                label: stepLabel,
                sublabel: step.data_source || step.signal_type || '',
                type: 'step',
                color: color,
                branch_index: branchIdx,
                is_key: !!step.is_key,
                source_times: step.source_times || []
            });
            edges.push({from_id: prevId, to_id: stepId});
            prevId = stepId;
        });

        var resultId = hypId + '-result';
        var resultLabel = status === 'confirmed' ? 'Root Cause' : status === 'rejected' ? '기각' : '진행 중';
        nodes.push({
            id: resultId,
            label: resultLabel,
            sublabel: hyp.status_reason || hyp.reason || '',
            type: 'result',
            color: color,
            branch_index: branchIdx,
            is_key: false
        });
        edges.push({from_id: prevId, to_id: resultId});

        if (hyp.leads_to != null) {
            for (var ti = 0; ti < hypotheses.length; ti++) {
                if ((hypotheses[ti].id || ti) == hyp.leads_to) {
                    edges.push({from_id: resultId, to_id: 'hyp-' + (hypotheses[ti].id || ti)});
                    break;
                }
            }
        }
    });
    return {nodes: nodes, edges: edges};
}

function render(el, data, opts) {
    opts = opts || {};
    var isReadOnly = opts.readOnly !== false;

    if (!data || !data.hypotheses || data.hypotheses.length === 0) {
        el.innerHTML = '';
        return;
    }

    var parsed = parse(data);
    var nodes = parsed.nodes;
    var hypotheses = data.hypotheses || [];
    var triageNode = nodes.find(function(n){return n.type === 'triage'});

    var html = '<div class="dag-container">';

    html += '<div class="dag-triage-col">';
    html += '<div class="dag-node dag-node-triage" title="' + _esc(triageNode.label) + '">';
    html += '<div class="dag-node-label">' + _esc(triageNode.label) + '</div>';
    html += '<div class="dag-node-sublabel">' + _esc(triageNode.sublabel) + '</div>';
    html += '</div></div>';

    html += '<div class="dag-branch-connector">&rarr;</div>';

    html += '<div class="dag-branches">';
    hypotheses.forEach(function(hyp, hi) {
        var branchIdx = hi + 1;
        var status = hyp.status || 'partial';
        var statusCss = STATUS_CSS[status] || 'dag-node-partial';
        var branchNodes = nodes.filter(function(n){return n.branch_index === branchIdx});

        html += '<div class="dag-branch">';
        branchNodes.forEach(function(node, ni) {
            var keyCss = node.is_key ? ' dag-node-key' : '';
            var titleText = node.label + (node.sublabel ? '\n' + node.sublabel : '');
            var times = node.source_times ? node.source_times.join(',') : '';
            var style = node.is_key ? 'border-color:' + node.color + ';' : '';
            if (times) style += 'cursor:pointer;';
            var onclick = (times && opts.onClickTimes) ? ' onclick="' + opts.onClickTimes + '(\'' + _esc(times) + '\')"' : '';
            html += '<div class="dag-node ' + statusCss + keyCss + '" title="' + _esc(titleText) + '" style="' + style + '"' + onclick + '>';
            html += '<div class="dag-node-label">' + _esc(node.label) + '</div>';
            if (node.sublabel) html += '<div class="dag-node-sublabel">' + _esc(node.sublabel) + '</div>';
            html += '</div>';
            if (ni < branchNodes.length - 1) html += '<div class="dag-connector">&rarr;</div>';
        });

        if (!isReadOnly && status === 'partial') {
            html += '<div class="dag-connector">&rarr;</div>';
            html += '<div class="dag-node dag-node-progress">';
            html += '<div class="dag-node-label">진행 중...</div>';
            html += '</div>';
        }

        html += '</div>';
    });
    html += '</div></div>';

    el.innerHTML = html;
}

return {parse: parse, render: render};
})();
