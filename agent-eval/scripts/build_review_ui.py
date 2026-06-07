#!/usr/bin/env python3
"""Generate a self-contained HTML review UI for human annotation (dark, two-pane,
navigable — matching the prior blind-pilot UI), pre-filled with oracle's draft.

The human reads each BLIND dialogue (rendered as color-coded turns) + oracle's
draft, then accepts or overrides each field. Overrides are tracked so we can
report "humans changed X% of drafts" as evidence the review was real. The UI
never shows our system score, so the human stays independent of our scorer.

Usage:
  python scripts/build_review_ui.py --drafts D:/tmp/oracle_calib_20260606/anchor_a.txt
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ANCHOR_DIR = PROJECT_DIR / "calibration" / "blind_v1" / "anchors"

FIELD_OPTS = {
    "goal_completion": ["yes", "mostly", "partial", "no", "unclear"],
    "instruction_compliance": ["yes", "minor", "major", "unclear"],
    "tool_correctness": ["correct", "minor", "wrong_tool", "wrong_args", "missing", "NA"],
    "db_state": ["correct", "partial", "incorrect", "harmful", "NA"],
    "fabrication": ["no", "yes", "unclear"],
    "internal_info_leak": ["no", "yes", "unclear"],
    "critical_veto": ["no", "yes"],
    "primary_failure_category": [
        "none",
        "internal_info_leak",
        "fake_tool_call",
        "wrong_tool_args",
        "wrong_db_state",
        "missed_required_tool",
        "instruction_violation",
        "unresolved_customer_goal",
        "unsafe_or_policy_violation",
        "communication_quality",
    ],
    "severity_bucket": [
        "90-100 干净通过",
        "75-89 小瑕疵",
        "60-74 部分成功",
        "40-59 严重失败",
        "20-39 关键失败",
        "0-19 灾难",
    ],
}
FIELD_LABELS = {
    "goal_completion": "目标完成",
    "instruction_compliance": "硬指令遵守",
    "tool_correctness": "工具调用",
    "db_state": "数据库/系统状态",
    "fabrication": "伪造/谎报工具",
    "internal_info_leak": "泄露内部信息",
    "critical_veto": "一票否决(veto)",
    "primary_failure_category": "主失败类别",
    "severity_bucket": "严重度档",
    "score": "总分 0-100",
    "rationale": "判分理由(一句话)",
}


def parse_drafts(raw: str) -> list[dict]:
    s, e = raw.find("["), raw.rfind("]")
    if s == -1 or e == -1:
        raise SystemExit("no JSON array found in oracle draft output")
    return json.loads(raw[s : e + 1])


_TOOL_MARKUP = re.compile(
    r"<\s*(minimax:tool_call|invoke\s+name=|tool_call\b|function_call\b)", re.I
)


def card_from_trace(aid: str, trace: dict, oracle: dict) -> dict:
    sc = trace.get("scenario") or {}
    msgs = []
    for m in ((trace.get("conversation") or {}).get("messages")) or []:
        content = (m.get("content") or "").strip()
        tools = []
        for c in m.get("tool_calls") or []:
            nm = c.get("name") or c.get("tool") or (c.get("function") or {}).get("name", "tool")
            args = c.get("arguments") or c.get("args") or (c.get("function") or {}).get("arguments")
            tools.append(
                {"name": str(nm), "args": json.dumps(args, ensure_ascii=False) if args else ""}
            )
        unparsed = (not tools) and bool(_TOOL_MARKUP.search(content))
        uname = ""
        if unparsed:
            mt = re.search(r'name="([^"]+)"', content)
            uname = mt.group(1) if mt else ""
        if content or tools:
            msgs.append(
                {
                    "turn": m.get("turn"),
                    "role": m.get("role", "?"),
                    "content": content,
                    "tools": tools,
                    "unparsed": unparsed,
                    "unparsed_name": uname,
                }
            )
    return {
        "id": aid,
        "scenario": {
            "name": sc.get("name", ""),
            "description": sc.get("description", ""),
            "user_goal": sc.get("user_goal", ""),
            "initial_message": sc.get("initial_message", ""),
            "constraints": sc.get("constraints") or [],
        },
        "messages": msgs,
        "oracle": oracle,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--out", default=str(ANCHOR_DIR / "review.html"))
    ap.add_argument("--title", default="锚点试标 · 8 条")
    args = ap.parse_args()

    drafts = parse_drafts(Path(args.drafts).read_text(encoding="utf-8"))
    (ANCHOR_DIR / "oracle_draft.json").write_text(
        json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    by_id = {d["anchor_id"]: d for d in drafts}

    amap = {}
    for line in (ANCHOR_DIR / "anchor_map.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            amap[r["anchor_id"]] = r

    cards = []
    for aid in sorted(amap):
        if aid not in by_id:
            continue
        trace = json.loads((PROJECT_DIR / amap[aid]["path"]).read_text(encoding="utf-8"))
        cards.append(card_from_trace(aid, trace, by_id[aid]))

    payload = json.dumps(
        {"cards": cards, "opts": FIELD_OPTS, "labels": FIELD_LABELS}, ensure_ascii=False
    )
    html = _HTML.replace("__TITLE__", args.title).replace("__PAYLOAD__", payload)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"review UI -> {args.out}  ({len(cards)} cards)")


_HTML = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#0a0a0a;color:#e0e0e0;padding-bottom:58px}
.header{background:#1a1a2e;padding:13px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;position:sticky;top:0;z-index:100;border-bottom:2px solid #2a2a44}
.header h1{font-size:16px;color:#64ffda;font-weight:600}
.header .sub{color:#888;font-size:13px;margin-left:6px}
.header .right{display:flex;gap:12px;align-items:center}
.header input{padding:7px 11px;background:#11111a;border:1px solid #333;border-radius:6px;color:#e0e0e0;font-size:13px}
.ovr{font-size:12px;color:#aaa}.ovr b{color:#f0a500}
.btn{padding:8px 16px;border:0;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;transition:.15s}
.btn-go{background:#1565c0;color:#fff}.btn-go:hover{background:#1976d2}
.btn-exp{background:#00bfa5;color:#062b25}.btn-exp:hover{background:#1de9b6}
.jump-wrap{background:#0d0d16;padding:8px 24px;border-bottom:1px solid #1c1c2c}
.jump-bar{display:flex;gap:6px;flex-wrap:wrap}
.jump-bar button{min-width:40px;height:28px;font-size:12px;padding:0 6px;border:1px solid #333;background:#16161f;color:#999;border-radius:5px;cursor:pointer;transition:.15s;position:relative}
.jump-bar button:hover{border-color:#555}
.jump-bar button.current{background:#1565c0;color:#fff;border-color:#1976d2}
.jump-bar button.touched{border-color:#00bfa5;color:#64ffda}
.jump-bar button.veto::after{content:'!';position:absolute;top:-5px;right:-4px;background:#e53935;color:#fff;font-size:9px;width:13px;height:13px;line-height:13px;border-radius:50%}
.container{max-width:1500px;margin:0 auto;padding:16px 20px;display:grid;grid-template-columns:1fr 420px;gap:18px}
@media(max-width:1000px){.container{grid-template-columns:1fr}}
.conversation{background:#101018;border-radius:12px;padding:18px;max-height:calc(100vh - 150px);overflow-y:auto}
.scenario-box{background:#16213e;padding:14px 16px;border-radius:9px;margin-bottom:16px;font-size:13px;line-height:1.8}
.scenario-box .lbl{color:#64ffda;font-weight:600;margin-right:5px}
.scenario-box .cons{margin-top:6px;color:#ffab91;font-size:12.5px}
.msg{margin:9px 0;padding:11px 14px;border-radius:9px;font-size:13.5px;line-height:1.75;animation:fade .2s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.msg.agent{background:#142117;border-left:3px solid #4caf50}
.msg.user{background:#10162b;border-left:3px solid #42a5f5}
.msg .rt{font-size:11px;font-weight:700;margin-bottom:4px;letter-spacing:.5px}
.msg.agent .rt{color:#66bb6a}.msg.user .rt{color:#64b5f6}
.msg .content{white-space:pre-wrap;word-break:break-word;color:#dcdcdc}
.tool-box{background:#1c1426;margin:7px 0 0;padding:7px 11px;border-radius:6px;font-size:12px;border-left:2px solid #ab47bc}
.tool-box .tn{color:#ce93d8;font-weight:600}.tool-box .ta{color:#8a8a8a;margin-top:2px;word-break:break-all;font-size:11px}
.msg.bad{background:#241015;border-left:3px solid #e53935}
.unparsed{color:#ffb3ba;font-size:12.5px;line-height:1.65}
.unparsed b{color:#ff8a80}
.unparsed details{margin-top:5px}
.unparsed summary{cursor:pointer;color:#ef9a9a;font-size:11px}
.unparsed pre{white-space:pre-wrap;color:#aaa;font-size:11px;margin-top:4px;background:#1a0d10;padding:6px 8px;border-radius:5px}
.scoring{background:#101018;border-radius:12px;padding:18px;max-height:calc(100vh - 150px);overflow-y:auto;position:sticky;top:78px}
.scoring.veto{box-shadow:inset 0 0 0 2px #e5393566;background:#160f10}
.scoring h3{color:#64ffda;font-size:15px;margin-bottom:6px}
.scoring .note{font-size:12px;color:#888;margin-bottom:14px;line-height:1.6}
.scoring h4{color:#9a9ab0;font-size:12px;margin:16px 0 9px;border-top:1px solid #20202e;padding-top:11px;letter-spacing:.5px}
.fld{margin-bottom:11px}
.fld label{display:block;font-size:12.5px;color:#bbb;margin-bottom:4px}
.fld select,.fld input,.fld textarea{width:100%;padding:8px 10px;background:#15151f;border:1px solid #2c2c3c;border-radius:6px;color:#e0e0e0;font-size:13px;font-family:inherit;transition:.15s}
.fld select:focus,.fld input:focus,.fld textarea:focus{border-color:#64ffda;outline:none}
.fld textarea{min-height:52px;resize:vertical}
.fld .oh{font-size:11px;color:#666;margin-top:3px}
.fld.changed select,.fld.changed input,.fld.changed textarea{border-color:#f0a500;background:#241f10}
.fld.changed .oh{color:#f0a500}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px}
.bottom{position:fixed;bottom:0;left:0;right:0;background:#1a1a2e;padding:9px 24px;display:flex;justify-content:space-between;align-items:center;border-top:2px solid #2a2a44;z-index:100}
.bottom .nav{display:flex;gap:8px}
.bottom button{padding:9px 18px;font-size:13px;border:1px solid #333;border-radius:6px;cursor:pointer;background:#2a2a3c;color:#e0e0e0;font-weight:600}
.bottom button:hover{background:#34344a}.bottom button:disabled{opacity:.3;cursor:default}
.bottom .save-hint{font-size:11px;color:#666}
</style></head><body>
<div class="header">
  <div><span></span><h1 style="display:inline">__TITLE__</h1><span class="sub" id="prog"></span></div>
  <div class="right">
    <span class="ovr" id="ovr"></span>
    <input id="who" placeholder="你的名字 (你 / serein431)">
    <button class="btn btn-exp" onclick="exportJSON()">导出标注 JSON</button>
  </div>
</div>
<div class="jump-wrap"><div class="jump-bar" id="jump"></div></div>
<div class="container">
  <div class="conversation" id="conv"></div>
  <div class="scoring" id="score"></div>
</div>
<div class="bottom">
  <span class="save-hint">改动本地自动暂存 · oracle 初判已填，只改你不同意的（变黄=记一次复核）· ← → 翻条</span>
  <div class="nav">
    <button id="prev" onclick="go(-1)">← 上一条</button>
    <button id="next" class="btn-go" onclick="go(1)" style="color:#fff;background:#1565c0;border-color:#1976d2">下一条 →</button>
  </div>
</div>
<script>
const DATA = __PAYLOAD__;
const KEY = "anchor_review_v2";
const STRUCT = ["goal_completion","instruction_compliance","tool_correctness","db_state","fabrication","internal_info_leak","critical_veto","primary_failure_category"];
let saved = JSON.parse(localStorage.getItem(KEY) || "{}");
let idx = 0;
const esc = s => (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const cur = (id,f) => { const s=saved[id]||{}; return (f in s)? s[f] : DATA.cards.find(c=>c.id===id).oracle[f]; };
const changedCount = id => { const o=DATA.cards.find(c=>c.id===id).oracle; let n=0;
  Object.keys(DATA.labels).forEach(f=>{ if(String(cur(id,f)??"")!==String(o[f]??"")) n++; }); return n; };

function renderJump(){
  const j=document.getElementById("jump"); j.innerHTML="";
  DATA.cards.forEach((c,i)=>{
    const b=document.createElement("button"); b.textContent=c.id;
    if(i===idx) b.classList.add("current");
    if(saved[c.id] && Object.keys(saved[c.id]).length) b.classList.add("touched");
    if(cur(c.id,"critical_veto")==="yes") b.classList.add("veto");
    b.onclick=()=>{idx=i;render()}; j.appendChild(b);
  });
}
function convHtml(c){
  let h=`<div class="scenario-box"><span class="lbl">场景</span>${esc(c.scenario.name)}<br>
    <span class="lbl">背景</span>${esc(c.scenario.description)}<br>
    <span class="lbl">客户目标</span>${esc(c.scenario.user_goal)}<br>
    <span class="lbl">外呼任务</span>${esc(c.scenario.initial_message)}`;
  if(c.scenario.constraints && c.scenario.constraints.length)
    h+=`<div class="cons">约束：${c.scenario.constraints.map(esc).join("、")}</div>`;
  h+=`</div>`;
  c.messages.forEach(m=>{
    const role = m.role==="agent"?"客服 Agent":(m.role==="user"?"客户":m.role);
    let t="";
    (m.tools||[]).forEach(tt=>{ t+=`<div class="tool-box"><span class="tn">⚙ 调用工具: ${esc(tt.name)}</span>${tt.args?`<div class="ta">${esc(tt.args)}</div>`:""}</div>`; });
    let body;
    if(m.unparsed){
      body=`<div class="unparsed">⚠ 未解析的工具调用语法 —— agent 想调用 <b>${esc(m.unparsed_name)||"工具"}</b>，但系统没接住、工具<b>实际未执行</b>；这串会被当成话术输出（真打电话客户会听到这堆乱码）。<details><summary>看原始内容</summary><pre>${esc(m.content)}</pre></details></div>`;
    } else {
      body = m.content?`<div class="content">${esc(m.content)}</div>`:"";
    }
    h+=`<div class="msg ${m.role}${m.unparsed?" bad":""}"><div class="rt">${esc(role)} · 第${m.turn}轮</div>${body}${t}</div>`;
  });
  return h;
}
function fldHtml(id,f){
  const o=DATA.cards.find(c=>c.id===id).oracle, val=cur(id,f), ov=o[f];
  const ch = String(val??"")!==String(ov??"");
  let ctrl;
  if(f==="score") ctrl=`<input type="number" min="0" max="100" data-f="${f}" value="${val??""}">`;
  else if(f==="rationale") ctrl=`<textarea data-f="${f}">${esc(val)}</textarea>`;
  else ctrl=`<select data-f="${f}">${DATA.opts[f].map(x=>`<option ${x===val?"selected":""}>${x}</option>`).join("")}</select>`;
  return `<div class="fld ${ch?"changed":""}"><label>${DATA.labels[f]}</label>${ctrl}<div class="oh">oracle: ${esc(String(ov))}</div></div>`;
}
function scoreHtml(c){
  const veto = cur(c.id,"critical_veto")==="yes";
  let h=`<h3>${c.id} 评分　<span style="font-size:12px;color:#888">已改 ${changedCount(c.id)} 处</span></h3>
    <div class="note">先读左边对话自己判断，再看 oracle 初判，<b style="color:#f0a500">不同意就改</b>。看不清证据选 unclear。</div>
    <h4>结构判断</h4><div class="grid2">`;
  STRUCT.forEach(f=>h+=fldHtml(c.id,f));
  h+=`</div><h4>总评</h4>${fldHtml(c.id,"severity_bucket")}
    <div class="grid2">${fldHtml(c.id,"score")}<div></div></div>${fldHtml(c.id,"rationale")}`;
  return h;
}
function render(){
  const c=DATA.cards[idx];
  document.getElementById("conv").innerHTML=convHtml(c);
  const sp=document.getElementById("score"); sp.innerHTML=scoreHtml(c);
  sp.classList.toggle("veto", cur(c.id,"critical_veto")==="yes");
  document.getElementById("conv").scrollTop=0; sp.scrollTop=0;
  bindInputs();
  document.getElementById("prev").disabled = idx===0;
  document.getElementById("next").disabled = idx===DATA.cards.length-1;
  renderJump(); updateProg();
}
function bindInputs(){
  document.querySelectorAll("#score [data-f]").forEach(el=>{
    el.addEventListener("input",()=>{
      const c=DATA.cards[idx]; let v=el.value; if(el.dataset.f==="score") v=v===""?null:Number(v);
      saved[c.id]=saved[c.id]||{}; saved[c.id][el.dataset.f]=v;
      localStorage.setItem(KEY,JSON.stringify(saved));
      const fld=el.closest(".fld"), ov=c.oracle[el.dataset.f];
      fld.classList.toggle("changed", String(v??"")!==String(ov??""));
      fld.querySelector(".oh").style.color = fld.classList.contains("changed")?"#f0a500":"#666";
      if(el.dataset.f==="critical_veto"){ document.getElementById("score").classList.toggle("veto",v==="yes"); }
      document.querySelector("#score h3 span").textContent=`已改 ${changedCount(c.id)} 处`;
      updateProg(); renderJump();
    });
  });
}
function updateProg(){
  let done=0,changed=0,total=0;
  DATA.cards.forEach(c=>{ if(cur(c.id,"rationale")) done++;
    Object.keys(DATA.labels).forEach(f=>{total++; if(String(cur(c.id,f)??"")!==String(c.oracle[f]??"")) changed++;});});
  document.getElementById("prog").textContent=`· 进度 ${done}/${DATA.cards.length}`;
  document.getElementById("ovr").innerHTML=`复核改动率 <b>${(100*changed/total).toFixed(0)}%</b> (${changed}/${total})`;
}
function go(d){ idx=Math.max(0,Math.min(DATA.cards.length-1,idx+d)); render(); }
document.addEventListener("keydown",e=>{ if(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")return;
  if(e.key==="ArrowLeft")go(-1); if(e.key==="ArrowRight")go(1); });
function exportJSON(){
  const who=document.getElementById("who").value.trim()||"anon";
  const rows=DATA.cards.map(c=>{ const o=c.oracle, human={}, changed={};
    Object.keys(DATA.labels).forEach(f=>{human[f]=cur(c.id,f); changed[f]=String(human[f]??"")!==String(o[f]??"");});
    return {anchor_id:c.id, annotator:who, human, oracle:o, changed};});
  const blob=new Blob([JSON.stringify(rows,null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=`anchor_labels_${who}.json`; a.click();
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
