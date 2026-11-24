"""A tiny self-contained web console served at ``/ui`` (no build step)."""

HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>磅单码配码 / 解码</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; padding: 24px; max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin: 0 0 8px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { border: 1px solid rgba(128,128,128,.35); border-radius: 10px; padding: 14px 16px; }
  textarea, input { width: 100%; font-family: ui-monospace, Menlo, Consolas, monospace;
         font-size: 12.5px; padding: 8px; border-radius: 6px; border: 1px solid rgba(128,128,128,.4); }
  textarea { min-height: 150px; }
  button { cursor: pointer; border: 0; border-radius: 6px; padding: 8px 14px;
         background: #2563eb; color: #fff; font-size: 13px; margin-right: 8px; }
  button.secondary { background: #64748b; }
  pre { background: rgba(127,127,127,.12); padding: 10px; border-radius: 6px;
        overflow: auto; max-height: 320px; font-size: 12px; white-space: pre-wrap; }
  .row { display: flex; gap: 8px; align-items: center; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#16a34a22;
          color:#16a34a; font-weight:600; }
  .muted { opacity: .7; font-size: 12px; }
  @media (max-width: 820px){ .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <h1>磅单码配码 / 解码控制台</h1>
  <p class="muted">码格式：首位标记（0-9）+ 后八位正文 ·
     <a href="/docs" target="_blank">Swagger 文档</a> ·
     <a href="/api/config" target="_blank">当前生效配置 JSON</a></p>
  <p>状态：<span id="health" class="pill">检查中…</span>
     <button class="secondary" onclick="loadConfig()">刷新配置</button></p>

  <div class="grid">
    <section class="card">
      <h2>解码 /api/decode</h2>
      <div class="row">
        <input id="code" value="110070123" placeholder="9 位磅单码，如 110070123"/>
        <button onclick="decode()">解码</button>
      </div>
      <pre id="decodeOut">（结果）</pre>
    </section>

    <section class="card">
      <h2>当前模板与标记</h2>
      <pre id="configOut">（加载中）</pre>
    </section>

    <section class="card">
      <h2>导入现用模板及标记 /api/import</h2>
      <textarea id="importBody"></textarea>
      <p>
        <button onclick="doImport(false)">替换导入</button>
        <button class="secondary" onclick="doImport(true)">追加导入</button>
        <button class="secondary" onclick="resetAll()">清空/复位</button>
      </p>
      <pre id="importOut"></pre>
    </section>

    <section class="card">
      <h2>批量登记并配码 /api/assign</h2>
      <textarea id="assignBody"></textarea>
      <p><button onclick="assign()">提交配码</button></p>
      <pre id="assignOut"></pre>
    </section>
  </div>

<script>
const IMPORT_EXAMPLE = {templates:[
  {name:"甲厂", marker:"1", prefix:"10",
   scale:{start:2,length:2}, weight:{start:4,length:4}},
  {name:"乙厂", marker:"2", prefix:"20",
   scale:{start:2,length:3}, weight:{start:5,length:3}}
]};
const ASSIGN_EXAMPLE = {templates:[
  {name:"丙厂", prefix:"30",
   scale:{start:2,length:2}, weight:{start:4,length:4}}
]};
document.getElementById('importBody').value = JSON.stringify(IMPORT_EXAMPLE,null,2);
document.getElementById('assignBody').value = JSON.stringify(ASSIGN_EXAMPLE,null,2);

async function send(method, url, body){
  const opt = {method, headers:{}};
  if(body !== undefined){ opt.headers['Content-Type']='application/json';
    opt.body = typeof body === 'string' ? body : JSON.stringify(body); }
  const r = await fetch(url, opt);
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch(e){ data = text; }
  return {status:r.status, data};
}
function show(id, r){
  document.getElementById(id).textContent =
    'HTTP ' + r.status + '\n' + JSON.stringify(r.data,null,2);
}
async function checkHealth(){
  const r = await send('GET','/health');
  const h = document.getElementById('health');
  if(r.status===200){ h.textContent = 'ok · version ' + r.data.active_version +
      ' · ' + r.data.template_count + ' 套模板'; }
  else { h.textContent = '服务异常'; h.style.background='#dc262622'; h.style.color='#dc2626'; }
}
async function loadConfig(){
  const r = await send('GET','/api/config');
  const d = r.data;
  let txt = 'markers: ' + JSON.stringify(d.used_markers) +
            '   count: ' + d.template_count + '/' + d.max_templates + '\n\n';
  txt += (d.templates||[]).map((t,i)=>
    `[${i}] marker=${t.marker} name=${t.name||''} prefix=${t.prefix} `+
    `scale=[${t.scale_start},+${t.scale_length}] weight=[${t.weight_start},+${t.weight_length}]`
  ).join('\n');
  document.getElementById('configOut').textContent = txt || '（空配置）';
  checkHealth();
}
async function decode(){
  const code = document.getElementById('code').value.trim();
  const r = await send('POST','/api/decode',{code});
  show('decodeOut', r);
}
async function doImport(append){
  const body = document.getElementById('importBody').value;
  const r = await send('POST','/api/import?mode='+(append?'append':'replace'), body);
  show('importOut', r); loadConfig();
}
async function assign(){
  const body = document.getElementById('assignBody').value;
  const r = await send('POST','/api/assign', body);
  show('assignOut', r); loadConfig();
}
async function resetAll(){
  if(!confirm('确定清空当前配置？')) return;
  const r = await send('POST','/api/admin/reset');
  show('importOut', r); loadConfig();
}
checkHealth(); loadConfig();
</script>
</body>
</html>
"""
