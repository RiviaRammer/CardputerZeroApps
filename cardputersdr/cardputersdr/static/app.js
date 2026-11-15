'use strict';
const $ = id => document.getElementById(id);
const worker = new Worker('/worker.js');
const canvas = $('spectrum'), ctx = canvas.getContext('2d');
const waterfall = $('waterfall'), wc = waterfall.getContext('2d');
const fftOptions = $('fft-size');
let fftSize = Number(fftOptions.value);
let socket, paused = false, state, previousConfig = '', reconnect;
const recentFrames = [];
let floor = -100, lastResult, busy = false, inFlight = false, controlsDirty = false;
function notice(text) { $('notice').textContent = text; }
function showStatus(s, initial = false) {
  state = s;
  $('connection').textContent = s.demo ? 'DEMO · 模拟信号' : s.running ? 'AIRSPY · 在线' : 'AIRSPY · 未就绪';
  $('web').classList.toggle('active', s.mode === 'web');
  $('lcd').classList.toggle('active', s.mode === 'lcd');
  $('lcd').disabled = s.lcd_available === false;
  if (fftOptions.disabled && s.fft_sizes?.length) {
    fftSize = s.fft_size;
    fftOptions.replaceChildren(...s.fft_sizes.map(size => new Option(size, size)));
    fftOptions.value = fftSize;
    fftOptions.disabled = false;
  }
  if (initial || !controlsDirty) {
    $('frequency').value = (s.frequency / 1e6).toFixed(3);
    $('rate').replaceChildren(...s.rates.map(r => new Option(`${r / 1e6} MS/s`, r)));
    $('rate').value = s.rate; $('gain').value = s.gain; $('gainValue').value = s.gain;
  }
  notice(s.error || (s.mode === 'lcd' ? 'LCD 模式 · 机身正在计算频谱，Web 画面暂停。' :
    `${s.demo ? 'DEMO 模拟信号 · ' : ''}I/Q 采样块 → 浏览器 FFT · 最高 ${s.fps} fps`));
}
async function control(values) {
  if (busy) return;
  busy = true; $('apply').disabled = true;
  try {
    const r = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(values)});
    const s = await r.json(); if (!r.ok) throw Error(s.error || r.statusText);
    controlsDirty = false; showStatus(s, true);
  } catch (e) { notice(e.message); }
  finally { busy = false; $('apply').disabled = false; }
}
$('apply').onclick = () => control({frequency:Math.round(Number($('frequency').value) * 1e6), rate:Number($('rate').value), gain:Number($('gain').value)});
$('frequency').onkeydown = e => { if (e.key === 'Enter') $('apply').click(); };
$('gain').oninput = () => { $('gainValue').value = $('gain').value; };
for (const id of ['frequency','rate','gain']) $(id).addEventListener('input',()=>{controlsDirty=true;});
$('web').onclick = () => control({mode:'web'});
$('lcd').onclick = () => control({mode:'lcd'});
$('retry').onclick = () => control({});
$('pause').onclick = () => { paused = !paused; $('pause').textContent = paused ? '继续画面' : '暂停画面'; if (!paused) next(); };
$('floor').oninput = () => { floor = Number($('floor').value); $('floorValue').value = `${floor} dBFS`; if(lastResult) draw(lastResult, false); };
fftOptions.onchange = () => {
  fftSize = Number(fftOptions.value);
  if (!paused) next();
};
function next() {
  if (!paused && !document.hidden && !inFlight && socket?.readyState === WebSocket.OPEN) {
    inFlight = true;
    socket.send(fftOptions.disabled ? 'next' : `next ${fftSize}`);
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) next(); });
function draw(result, scroll = true) {
  const {db, frequency, rate, gain, dropped} = result, width = canvas.width, height = canvas.height;
  const config = `${frequency}/${rate}/${gain}/${db.length}`;
  if (config !== previousConfig) { wc.clearRect(0, 0, waterfall.width, waterfall.height); previousConfig = config; }
  ctx.fillStyle = '#0b141f'; ctx.fillRect(0, 0, width, height);
  ctx.font = '12px monospace';
  for (let v = floor; v <= 0; v += 20) {
    const y = height - (v-floor) / -floor * (height-20);
    ctx.strokeStyle = '#1c3042'; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(width,y); ctx.stroke();
    ctx.fillStyle = '#6a859b'; ctx.fillText(`${v}`, 6, Math.max(13, y-4));
  }
  for (let x=0; x<=width; x+=width/8) { ctx.strokeStyle='#172b3b'; ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke(); }
  let peak = -Infinity, peakBin = 0;
  const row = wc.createImageData(width, 1);
  ctx.beginPath();
  for (let x=0; x<width; x++) {
    let value = -140;
    const start = Math.floor(x*db.length/width), end = Math.max(start+1, Math.floor((x+1)*db.length/width));
    for(let k=start;k<end;k++) { value=Math.max(value,db[k]); if(db[k]>peak){peak=db[k];peakBin=k;} }
    const level = Math.max(0, Math.min(1,(value-floor)/-floor));
    const y = height-level*(height-20); if(x===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
    const t = Math.min(1,level*1.6), p=x*4;
    row.data[p] = Math.round(255*Math.max(0,(t-.65)/.35));
    row.data[p+1] = Math.round(230*Math.min(1,t*1.7));
    row.data[p+2] = Math.round(25+180*Math.sin(t*Math.PI)); row.data[p+3]=255;
  }
  ctx.strokeStyle='#53e0c4';ctx.lineWidth=1.6;ctx.stroke();
  if (scroll) { wc.drawImage(waterfall,0,0,width,waterfall.height-1,0,1,width,waterfall.height-1); wc.putImageData(row,0,0); }
  $('axis').replaceChildren(...[0,.25,.5,.75,1].map(p=>{const span=document.createElement('span');span.textContent=((frequency+(p-.5)*rate)/1e6).toFixed(3);return span;}));
  $('peak').textContent=`${((frequency+(peakBin/db.length-.5)*rate)/1e6).toFixed(4)} MHz · ${peak.toFixed(1)} dBFS`;
  const fps = recentFrames.length > 1 ? (recentFrames.length-1)*1000/(recentFrames.at(-1)-recentFrames[0]) : 0;
  $('stats').textContent=`${rate/1e6} MS/s · ${(rate/db.length).toFixed(0)} Hz/bin · ${fps.toFixed(1)} fps · USB 丢样 ${dropped}`;
}
worker.onmessage = ({data}) => {
  inFlight = false;
  if (data.error) notice(data.error);
  else if (data.db.length === fftSize) {
    const now=performance.now(); recentFrames.push(now);
    while(recentFrames.length && recentFrames[0]<now-2000) recentFrames.shift();
    lastResult=data; draw(data);
  }
  next();
};
worker.onerror = e => notice(`FFT worker: ${e.message}`);
function connect() {
  clearTimeout(reconnect);
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/iq`);
  socket.binaryType='arraybuffer';
  socket.onopen=()=>{recentFrames.length=0;inFlight=false;next();};
  socket.onmessage=({data})=>{
    if(typeof data==='string'){inFlight=false;showStatus(JSON.parse(data));setTimeout(next,100);}
    else worker.postMessage(data,[data]);
  };
  socket.onclose=()=>{ inFlight=false; $('connection').textContent='连接断开'; notice('等待 CardputerSDR 重新连接…'); reconnect=setTimeout(connect,2000); };
  socket.onerror=()=>socket.close();
}
fetch('/api/status').then(r=>r.json()).then(s=>{showStatus(s,true);connect();}).catch(e=>{notice(e.message);reconnect=setTimeout(()=>location.reload(),3000);});
setInterval(()=>{ if(!document.hidden) fetch('/api/status').then(r=>r.json()).then(s=>showStatus(s)).catch(()=>{}); },3000);
