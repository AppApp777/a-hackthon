"""Generate a standalone HTML annotation UI from gold_items.jsonl."""

import json
from pathlib import Path

JSONL_PATH = Path(__file__).parent / "gold_items.jsonl"
OUT_PATH = Path(__file__).parent / "annotate.html"

items = []
with open(JSONL_PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            items.append(json.loads(line))

items_json = json.dumps(items, ensure_ascii=False)

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>人工标注工具 — 123 项</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 20px; }
.header { display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid #333; margin-bottom: 20px; }
.header h1 { font-size: 20px; }
.progress-bar-outer { width: 300px; height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
.progress-bar-inner { height: 100%; background: #4caf50; transition: width 0.3s; }
.stats { font-size: 14px; color: #999; display: flex; gap: 20px; }
.stats span { color: #4caf50; font-weight: bold; }

.card { background: #1a1a1a; border-radius: 12px; padding: 24px; margin-bottom: 20px; border: 1px solid #2a2a2a; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.badge { padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.badge-dimension { background: #1a3a5c; color: #64b5f6; }
.badge-binary { background: #1a3c1a; color: #81c784; }
.badge-easy { background: #2e4a1a; color: #aed581; }
.badge-medium { background: #4a3a1a; color: #ffd54f; }
.badge-hard { background: #4a1a1a; color: #ef9a9a; }
.badge-extreme { background: #3a1a3a; color: #ce93d8; }

.scenario-name { font-size: 18px; font-weight: 600; color: #fff; }
.scenario-desc { font-size: 14px; color: #999; margin-bottom: 16px; }

.conversation { background: #111; border-radius: 8px; padding: 16px; margin-bottom: 16px; max-height: 400px; overflow-y: auto; font-size: 14px; line-height: 1.8; }
.conv-turn { margin-bottom: 8px; }
.conv-agent { color: #64b5f6; }
.conv-user { color: #ffd54f; }

.rubric-box { background: #1a2a1a; border-left: 3px solid #4caf50; padding: 12px 16px; border-radius: 4px; margin-bottom: 16px; font-size: 14px; line-height: 1.6; }
.rubric-label { color: #81c784; font-weight: 600; margin-bottom: 4px; }

.item-name { font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 8px; }

.system-score { font-size: 13px; color: #888; margin-bottom: 16px; }
.system-score strong { color: #64b5f6; }

.buttons { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.btn { padding: 10px 20px; border-radius: 8px; border: 2px solid #444; background: #222; color: #ddd; font-size: 15px; cursor: pointer; transition: all 0.15s; min-width: 60px; text-align: center; }
.btn:hover { border-color: #888; background: #333; }
.btn.selected { border-color: #4caf50; background: #1a3a1a; color: #4caf50; font-weight: 700; }
.btn-true { min-width: 120px; }
.btn-false { min-width: 120px; }
.btn-skip { border-color: #555; color: #888; }
.btn-skip.selected { border-color: #ff9800; background: #3a2a1a; color: #ff9800; }

.nav { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; }
.nav-btn { padding: 10px 24px; border-radius: 8px; border: 1px solid #444; background: #222; color: #ddd; font-size: 14px; cursor: pointer; }
.nav-btn:hover { background: #333; }
.nav-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.nav-index { color: #888; font-size: 14px; }

.export-area { margin-top: 20px; }
.export-btn { padding: 12px 32px; border-radius: 8px; border: none; background: #4caf50; color: #fff; font-size: 16px; font-weight: 600; cursor: pointer; }
.export-btn:hover { background: #66bb6a; }

.filter-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-btn { padding: 6px 14px; border-radius: 16px; border: 1px solid #444; background: #1a1a1a; color: #aaa; font-size: 13px; cursor: pointer; }
.filter-btn.active { border-color: #4caf50; color: #4caf50; background: #1a2a1a; }

.done-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: flex; justify-content: center; align-items: center; z-index: 100; }
.done-box { background: #1a1a1a; border-radius: 16px; padding: 40px; text-align: center; max-width: 500px; }
.done-box h2 { font-size: 24px; color: #4caf50; margin-bottom: 16px; }

.explanation { font-size: 13px; color: #777; margin-top: 8px; padding: 8px 12px; background: #151515; border-radius: 6px; line-height: 1.6; }
</style>
</head>
<body>

<div class="header">
  <h1>人工标注工具</h1>
  <div>
    <div class="stats">
      <div>已标 <span id="doneCount">0</span> / 123</div>
      <div>二元 <span id="binaryDone">0</span>/57</div>
      <div>维度 <span id="dimDone">0</span>/66</div>
    </div>
    <div class="progress-bar-outer" style="margin-top:8px">
      <div class="progress-bar-inner" id="progressBar" style="width:0%"></div>
    </div>
  </div>
</div>

<div class="filter-bar">
  <button class="filter-btn active" onclick="setFilter('all')">全部 (123)</button>
  <button class="filter-btn" onclick="setFilter('binary')">二元题优先 (57)</button>
  <button class="filter-btn" onclick="setFilter('dimension')">维度题 (66)</button>
  <button class="filter-btn" onclick="setFilter('unlabeled')">未标注</button>
</div>

<div id="cardArea"></div>

<div class="nav">
  <button class="nav-btn" id="prevBtn" onclick="go(-1)">← 上一条</button>
  <span class="nav-index" id="navIndex">1 / 123</span>
  <button class="nav-btn" id="nextBtn" onclick="go(1)">下一条 →</button>
</div>

<div class="export-area" style="text-align:center; margin-top: 30px;">
  <button class="export-btn" onclick="exportResults()">导出标注结果 (JSONL)</button>
  <div style="font-size:13px; color:#888; margin-top:8px;">导出后保存为 gold_items.jsonl 替换原文件</div>
</div>

<div class="done-overlay" id="doneOverlay" style="display:none">
  <div class="done-box">
    <h2>标注完成！</h2>
    <p style="color:#ccc; margin-bottom:20px;">你标完了所有条目。点击下方按钮导出。</p>
    <button class="export-btn" onclick="exportResults()">导出标注结果</button>
    <br><br>
    <button class="nav-btn" onclick="document.getElementById('doneOverlay').style.display='none'">继续检查</button>
  </div>
</div>

<script>
const ALL_ITEMS = __ITEMS_PLACEHOLDER__;

let labels = {};
let currentIdx = 0;
let filteredIndices = ALL_ITEMS.map((_, i) => i);
let currentFilter = 'all';

// Load saved progress from localStorage
const STORAGE_KEY = 'annotation_labels_v1';
try {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) labels = JSON.parse(saved);
} catch(e) {}

function saveProgress() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(labels)); } catch(e) {}
}

function setFilter(f) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  if (f === 'all') filteredIndices = ALL_ITEMS.map((_, i) => i);
  else if (f === 'binary') filteredIndices = ALL_ITEMS.map((_, i) => i).filter(i => ALL_ITEMS[i].item_type === 'binary');
  else if (f === 'dimension') filteredIndices = ALL_ITEMS.map((_, i) => i).filter(i => ALL_ITEMS[i].item_type === 'dimension');
  else if (f === 'unlabeled') filteredIndices = ALL_ITEMS.map((_, i) => i).filter(i => labels[i] === undefined);

  currentIdx = 0;
  render();
}

function formatConversation(summary) {
  if (!summary) return '';
  return summary.split('\n').map(line => {
    line = line.replace(/\.\.\. \(中间省略\) \.\.\./, '<span style="color:#666;font-style:italic">... (中间省略) ...</span>');
    if (line.match(/^\[轮\d+\] Agent:/)) {
      return '<div class="conv-turn conv-agent">' + escapeHtml(line) + '</div>';
    } else if (line.match(/^\[轮\d+\] 用户:/)) {
      return '<div class="conv-turn conv-user">' + escapeHtml(line) + '</div>';
    } else {
      return '<div class="conv-turn">' + escapeHtml(line) + '</div>';
    }
  }).join('');
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function render() {
  if (filteredIndices.length === 0) {
    document.getElementById('cardArea').innerHTML = '<div class="card"><p style="text-align:center;color:#888;">没有匹配的条目</p></div>';
    document.getElementById('navIndex').textContent = '0 / 0';
    return;
  }
  if (currentIdx >= filteredIndices.length) currentIdx = filteredIndices.length - 1;
  if (currentIdx < 0) currentIdx = 0;

  const realIdx = filteredIndices[currentIdx];
  const item = ALL_ITEMS[realIdx];
  const isBinary = item.item_type === 'binary';

  const diffColors = { easy: 'easy', medium: 'medium', hard: 'hard', extreme: 'extreme' };

  let html = '<div class="card">';
  html += '<div class="card-header">';
  html += '<div>';
  html += '<span style="color:#4caf50;font-weight:700;font-size:16px;margin-right:10px;">#' + (realIdx + 1) + '</span>';
  html += '<span class="badge ' + (isBinary ? 'badge-binary' : 'badge-dimension') + '">' + (isBinary ? '二元判断' : '维度评分') + '</span> ';
  html += '<span class="badge badge-' + (diffColors[item.difficulty] || 'medium') + '">' + item.difficulty + '</span>';
  html += '</div>';
  html += '<div class="scenario-name">' + escapeHtml(item.scenario_name) + '</div>';
  html += '</div>';

  if (item.scenario_description) {
    html += '<div class="scenario-desc">' + escapeHtml(item.scenario_description) + '</div>';
  }

  // Show context panels based on which dimension is being annotated
  const dimId = item.item_id;

  if (dimId === 'D1' && item.instruction_steps) {
    html += '<div class="rubric-box" style="background:#1a1a2a; border-left-color:#64b5f6;"><div class="rubric-label" style="color:#64b5f6;">📋 正确步骤顺序（D1 参考）</div>' + escapeHtml(item.instruction_steps).replace(/\n/g, '<br>') + '</div>';
  }

  if (dimId === 'D2' && item.call_context) {
    html += '<div class="rubric-box" style="background:#1a1a2a; border-left-color:#64b5f6;"><div class="rubric-label" style="color:#64b5f6;">📋 Agent 掌握的信息（D2 参考：这些是应该确认的）</div>' + escapeHtml(item.call_context).replace(/\n/g, '<br>') + '</div>';
  }

  if (dimId === 'D3' && item.forbidden_behaviors) {
    html += '<div class="rubric-box" style="background:#2a1a1a; border-left-color:#ef9a9a;"><div class="rubric-label" style="color:#ef9a9a;">🚫 禁止话术（D3 参考）</div>' + escapeHtml(item.forbidden_behaviors) + '</div>';
  }

  if (dimId === 'D4' && item.callee_context) {
    html += '<div class="rubric-box" style="background:#1a2a1a; border-left-color:#ffd54f;"><div class="rubric-label" style="color:#ffd54f;">👤 被叫方背景（D4 参考：可能触发的异常）</div>' + escapeHtml(item.callee_context).replace(/\n/g, '<br>') + '</div>';
  }

  if (dimId === 'D6') {
    if (item.forbidden_behaviors) {
      html += '<div class="rubric-box" style="background:#2a1a1a; border-left-color:#ef9a9a;"><div class="rubric-label" style="color:#ef9a9a;">🚫 禁止行为（D6 参考）</div>' + escapeHtml(item.forbidden_behaviors) + '</div>';
    }
    if (item.call_context) {
      html += '<div class="rubric-box" style="background:#1a1a2a; border-left-color:#64b5f6;"><div class="rubric-label" style="color:#64b5f6;">📋 权限范围（D6 参考）</div>' + escapeHtml(item.call_context).replace(/\n/g, '<br>') + '</div>';
    }
  }

  // Binary items: show all context
  if (isBinary) {
    if (item.instruction_steps) {
      html += '<div class="rubric-box" style="background:#1a1a2a; border-left-color:#64b5f6;"><div class="rubric-label" style="color:#64b5f6;">📋 场景步骤</div>' + escapeHtml(item.instruction_steps).replace(/\n/g, '<br>') + '</div>';
    }
    if (item.forbidden_behaviors) {
      html += '<div class="rubric-box" style="background:#2a1a1a; border-left-color:#ef9a9a;"><div class="rubric-label" style="color:#ef9a9a;">🚫 禁止行为</div>' + escapeHtml(item.forbidden_behaviors) + '</div>';
    }
  }

  html += '<div class="conversation">' + formatConversation(item.conversation_summary) + '</div>';

  html += '<div class="item-name">📋 ' + escapeHtml(item.item_name) + '</div>';

  if (item.rubric) {
    html += '<div class="rubric-box"><div class="rubric-label">评分标准</div>' + escapeHtml(item.rubric) + '</div>';
  }

  html += '<div style="margin-top:16px; margin-bottom:8px; font-size:14px; color:#aaa;">你的标注：</div>';

  const hasLabeled = (labels[realIdx] !== undefined);
  const systemBlock = '<div class="system-score" style="margin-top:12px;">系统判断：<strong>' + JSON.stringify(item.system_score) + '</strong></div>'
    + (item.system_explanation ? '<div class="explanation">系统解释：' + escapeHtml(item.system_explanation) + '</div>' : '');

  const currentLabel = labels[realIdx];

  if (isBinary) {
    html += '<div class="buttons">';
    html += '<button class="btn btn-true ' + (currentLabel === true ? 'selected' : '') + '" onclick="setLabel(' + realIdx + ', true)">✅ 是 (true)</button>';
    html += '<button class="btn btn-false ' + (currentLabel === false ? 'selected' : '') + '" onclick="setLabel(' + realIdx + ', false)">❌ 否 (false)</button>';
    html += '<button class="btn btn-skip ' + (currentLabel === -1 ? 'selected' : '') + '" onclick="setLabel(' + realIdx + ', -1)">跳过</button>';
    html += '</div>';
  } else {
    html += '<div class="buttons">';
    for (let s = 0; s <= 5; s++) {
      html += '<button class="btn ' + (currentLabel === s ? 'selected' : '') + '" onclick="setLabel(' + realIdx + ', ' + s + ')">' + s + ' 分</button>';
    }
    html += '<button class="btn btn-skip ' + (currentLabel === -1 ? 'selected' : '') + '" onclick="setLabel(' + realIdx + ', -1)">跳过</button>';
    html += '</div>';
  }

  if (hasLabeled) {
    html += systemBlock;
  } else {
    html += '<div style="margin-top:12px; font-size:13px; color:#555; font-style:italic;">（标注后显示系统判断）</div>';
  }

  html += '</div>';

  document.getElementById('cardArea').innerHTML = html;
  document.getElementById('navIndex').textContent = '#' + (realIdx + 1) + ' (' + (currentIdx + 1) + '/' + filteredIndices.length + ')';
  document.getElementById('prevBtn').disabled = currentIdx <= 0;
  document.getElementById('nextBtn').disabled = currentIdx >= filteredIndices.length - 1;

  updateStats();
}

function setLabel(realIdx, value) {
  labels[realIdx] = value;
  saveProgress();
  updateStats();
  // Auto-advance after 300ms
  setTimeout(() => {
    if (currentIdx < filteredIndices.length - 1) {
      currentIdx++;
      render();
    } else {
      render(); // re-render current to show selected state
      checkAllDone();
    }
  }, 300);
}

function go(delta) {
  currentIdx += delta;
  render();
}

function updateStats() {
  const total = Object.keys(labels).filter(k => labels[k] !== undefined && labels[k] !== -1).length;
  const binaryTotal = ALL_ITEMS.map((_, i) => i).filter(i => ALL_ITEMS[i].item_type === 'binary' && labels[i] !== undefined && labels[i] !== -1).length;
  const dimTotal = ALL_ITEMS.map((_, i) => i).filter(i => ALL_ITEMS[i].item_type === 'dimension' && labels[i] !== undefined && labels[i] !== -1).length;

  document.getElementById('doneCount').textContent = total;
  document.getElementById('binaryDone').textContent = binaryTotal;
  document.getElementById('dimDone').textContent = dimTotal;
  document.getElementById('progressBar').style.width = (total / 123 * 100) + '%';
}

function checkAllDone() {
  const total = Object.keys(labels).filter(k => labels[k] !== undefined && labels[k] !== -1).length;
  if (total >= 123) {
    document.getElementById('doneOverlay').style.display = 'flex';
  }
}

function exportResults() {
  const lines = ALL_ITEMS.map((item, i) => {
    const copy = { ...item };
    if (labels[i] !== undefined && labels[i] !== -1) {
      copy.human_label = labels[i];
    }
    return JSON.stringify(copy, null, 0);
  });
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'application/jsonl' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'gold_items.jsonl';
  a.click();
  URL.revokeObjectURL(url);
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') go(-1);
  else if (e.key === 'ArrowRight') go(1);
  else if (e.key >= '0' && e.key <= '5') {
    const realIdx = filteredIndices[currentIdx];
    if (ALL_ITEMS[realIdx].item_type === 'dimension') setLabel(realIdx, parseInt(e.key));
  }
  else if (e.key === 'y' || e.key === 'Y') {
    const realIdx = filteredIndices[currentIdx];
    if (ALL_ITEMS[realIdx].item_type === 'binary') setLabel(realIdx, true);
  }
  else if (e.key === 'n' || e.key === 'N') {
    const realIdx = filteredIndices[currentIdx];
    if (ALL_ITEMS[realIdx].item_type === 'binary') setLabel(realIdx, false);
  }
  else if (e.key === 's' || e.key === 'S') {
    const realIdx = filteredIndices[currentIdx];
    setLabel(realIdx, -1);
  }
});

render();
</script>
</body>
</html>"""

html_out = HTML.replace("__ITEMS_PLACEHOLDER__", items_json)

OUT_PATH.write_text(html_out, encoding="utf-8")
print(f"Generated {OUT_PATH} ({len(items)} items, {OUT_PATH.stat().st_size / 1024:.0f} KB)")
