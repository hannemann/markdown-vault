"""Knowledge-graph render component — a WebKit WebView with an inline,
self-contained, canvas-rendered force-directed layout (no CDN, no external assets).

This is a *separate* WebView from the Markdown preview: it runs its own page
scripts under a permissive-but-local CSP (``script-src 'unsafe-inline'``, no
network), whereas the preview stays ``script-src 'none'``.

Nodes and edges are drawn to a single ``<canvas>`` (one element, not thousands of
SVG nodes), which scales to a few thousand nodes without the per-element
rasterisation that makes SVG crawl. Node labels are drawn with ``fillText``
(pixels, never HTML); the hover tooltip is HTML but sets the untrusted
title/description via ``textContent``, so there is no injection surface.

Usage:
    view = GraphView()
    view.connect("node-activated", lambda _v, path: open_file(path))
    view.set_graph(payload)          # {"nodes": [...], "edges": [...]}
"""

import json
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, WebKit, GObject, GLib, Adw

from markdown_vault.core.i18n import _
from markdown_vault.markdown import frontmatter

logger = logging.getLogger(__name__)


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#000;
    font:12px system-ui,sans-serif;color:var(--fg,#888)}
  #cv{display:block;width:100%;height:100%;cursor:grab;touch-action:none}
  #cv.grabbing{cursor:grabbing}
  /* Collapsible, scrollable legend box, top-left. Many communities would otherwise
     paper over the graph, so the list scrolls inside a bounded box and the header
     folds it away. */
  #legend{position:absolute;left:8px;top:8px;font-size:11px;max-width:240px;
    background:var(--legend-bg,Canvas);color:var(--legend-fg,CanvasText);
    border:1px solid var(--legend-border,rgba(127,127,127,.45));border-radius:6px;
    pointer-events:auto;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.4)}
  #legendhd{padding:4px 9px;cursor:pointer;font-weight:600;user-select:none;
    display:flex;align-items:center;gap:6px;opacity:.9}
  #legendhd::before{content:"▾";font-size:9px;opacity:.7}
  #legend.collapsed #legendhd::before{content:"▸"}
  #legenditems{max-height:42vh;overflow-y:auto;padding:1px 9px 6px}
  #legend.collapsed #legenditems{display:none}
  #legenditems .row{display:flex;align-items:center;gap:5px;white-space:nowrap;
    padding:1px 4px;line-height:1.5;cursor:pointer;border-radius:3px}
  #legenditems .row:hover{background:rgba(127,127,127,.16)}
  #legenditems .row.active{background:rgba(127,127,127,.30)}
  /* box-shadow, not border, keeps the 9x9 geometry — the swatch keeps its exact
     colour (so it still matches the node in the graph) but always has an edge, even
     a pale one on a light legend in a light GTK theme (ZW1). The ring uses its own
     --swatch-ring (a stronger alpha than the subtle panel outline), so it stays
     perceptible against the very swatches it is there for (ZX1). */
  #legenditems i{width:9px;height:9px;border-radius:50%;flex:none;
    box-shadow:0 0 0 1px var(--swatch-ring,rgba(127,127,127,.55))}
  #empty{position:absolute;inset:0;display:none;align-items:center;
    justify-content:center;color:var(--dim,#999);font-style:italic}
  /* Node info: a fixed panel in the top-right corner (mirror of the legend), so it
     never occludes the hovered node the way a pointer-anchored tooltip did. */
  #tip{position:absolute;display:none;right:8px;top:8px;pointer-events:none;
    max-width:260px;max-height:42vh;overflow-y:auto;
    background:var(--tip-bg,Canvas);color:var(--tip-fg,CanvasText);
    border:1px solid var(--tip-border,rgba(127,127,127,.45));
    border-radius:6px;padding:6px 9px;line-height:1.35;z-index:10;
    box-shadow:0 2px 12px rgba(0,0,0,.45)}
  #tip .tip-title{font-weight:600}
  #tip .tip-desc{opacity:.8;margin-top:2px;white-space:normal;
    overflow-wrap:anywhere}
  #tip .tip-vault{display:flex;align-items:center;gap:5px;opacity:.7;
    font-size:.9em;margin-top:5px}
  #tip .tip-vault i{width:8px;height:8px;border-radius:50%;flex:none;
    box-shadow:0 0 0 1px var(--swatch-ring,rgba(127,127,127,.55))}
</style></head>
<body>
<canvas id="cv"></canvas>
<div id="legend"><div id="legendhd">Legend</div><div id="legenditems"></div></div>
<div id="empty">no connections</div>
<div id="tip"></div>
<script>
const cv=document.getElementById("cv"), ctx=cv.getContext("2d");
const legend=document.getElementById("legend"), empty=document.getElementById("empty");
const legendItems=document.getElementById("legenditems");
const legendHd=document.getElementById("legendhd");
legendHd.addEventListener("click",()=>legend.classList.toggle("collapsed"));
const tip=document.getElementById("tip");
let W=innerWidth,H=innerHeight,cx=W/2,cy=H/2,dpr=window.devicePixelRatio||1;
let nodes=[],links=[],byId={},centerId=null,adj={};
let tx=0,ty=0,scale=1,alpha=0,raf=0,fitPending=false,zraf=0;
let tagFilter=[],searchQ="",hoverId=null,hoverTarget=null,hoverTimer=0;
let focusColor=null,focusRep=null,topColor=null;
// Hover tooltip: 500ms-debounced, lazily resolved by the host and cached per id.
let tipTimer=0,tipFor=null;
const tipCache={};
// Theme colours the canvas draws with — kept in JS (setTheme mirrors them from the
// host's CSS variables), since a canvas can't read CSS custom properties.
let theme={edge:"rgba(136,136,136,.5)",edgeOut:"rgba(91,155,213,.85)",
  nodeRing:"rgba(0,0,0,.35)",accent:"#e66100",fg:"#888",halo:"rgba(127,127,127,.35)"};
// Chrome strings — English fallbacks; the host replaces them with gettext translations
// via setStrings (the WebView can't call gettext itself).
let strings={legend:"Legend",noConnections:"no connections"};

function radius(n){return 3.5+Math.min(20,Math.sqrt(n.degree||0)*2.4)+(n.center?4:0);}
// Sphere shading: cached light/dark shades of the node colour for the radial gradient.
function litOf(hex){const v=parseInt(hex.slice(1),16),r=(v>>16)&255,g=(v>>8)&255,b=v&255;
  const m=x=>Math.round(x+(255-x)*0.55); return "rgb("+m(r)+","+m(g)+","+m(b)+")";}
function drkOf(hex){const v=parseInt(hex.slice(1),16),r=(v>>16)&255,g=(v>>8)&255,b=v&255;
  const m=x=>Math.round(x*0.62); return "rgb("+m(r)+","+m(g)+","+m(b)+")";}
function base(p){const q=p.replace(/\/+$/,"").split("/");return q[q.length-1]||p;}
function label(n){return n.label||base(n.id);}
function passTag(n){return tagFilter.length===0||(n.tags||[]).some(t=>tagFilter.indexOf(t)>=0);}
function vis(n){return n&&passTag(n);}
function matchSearch(n){return !searchQ||label(n).toLowerCase().indexOf(searchQ)>=0
  ||(n.id||"").toLowerCase().indexOf(searchQ)>=0;}
function nearHover(id){return hoverId===id||(adj[hoverId]&&adj[hoverId].has(id));}
// A node is dimmed if the search excludes it OR a hover excludes it — both
// contribute so leaving a hover restores (not clears) the search dim.
function faded(n){
  return (searchQ&&!matchSearch(n))||(hoverId&&!nearHover(n.id))
    ||(focusColor&&n.color!==focusColor);   // legend focus dims other communities
}

// --- Canvas sizing & drawing --------------------------------------------
function resize(){
  dpr=window.devicePixelRatio||1; W=innerWidth;H=innerHeight;cx=W/2;cy=H/2;
  cv.width=Math.round(W*dpr); cv.height=Math.round(H*dpr);
  cv.style.width=W+"px"; cv.style.height=H+"px";
  draw();
}
addEventListener("resize",()=>{resize();alpha=Math.max(alpha,0.3);fitPending=true;kick();});

function strokeEdges(pred,style,w){  // one path, one stroke = one draw call for all matches
  ctx.lineWidth=w; ctx.strokeStyle=style; ctx.beginPath();
  for(let i=0;i<links.length;i++){const e=links[i];
    const a=byId[e.source],b=byId[e.target];
    if(!vis(a)||!vis(b))continue;
    if(pred&&!pred(e,a,b))continue;
    ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y);}
  ctx.stroke();
}
function draw(){
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,W,H);
  ctx.translate(tx,ty); ctx.scale(scale,scale);
  // Edges. Normal edges batch into one path; when a hover/search is active they
  // split into a dim pass (all) + a bright pass (both endpoints in focus). Out-edges
  // (from the centre node) glow bright, but only to endpoints still in focus — a
  // hover or search dims the centre's edges to unrelated/non-matching nodes too.
  if(hoverId){
    // An active hover wins over the permanent centre emphasis: dim every edge,
    // then light only the hovered node's incident edges. Otherwise the centre's
    // out-edges glow bright regardless and read as belonging to the hovered node
    // when they connect the centre to unrelated nodes.
    ctx.globalAlpha=0.10; strokeEdges(null, theme.edge, 1.1);
    ctx.globalAlpha=1;
    strokeEdges((e)=>e.source===hoverId||e.target===hoverId, theme.edgeOut, 1.6);
  } else {
    if(searchQ||focusColor){
      ctx.globalAlpha=0.12; strokeEdges(e=>e.source!==centerId, theme.edge, 1.3);
      ctx.globalAlpha=1;
      strokeEdges((e,a,b)=>e.source!==centerId&&!faded(a)&&!faded(b), theme.edge, 1.3);
    } else {
      strokeEdges(e=>e.source!==centerId, theme.edge, 1.3);
    }
    // Centre out-edges: faded-aware so a search dims the centre's edges to
    // non-matching nodes too (without a search/focus, faded() is false for all,
    // so the full fan glows as before). Matches the hover rule above (ZX2).
    if(centerId) strokeEdges((e,a,b)=>e.source===centerId&&!faded(a)&&!faded(b),
      theme.edgeOut, 1.6);
  }
  ctx.globalAlpha=1;
  // Nodes as shaded spheres (radial gradient: highlight top-left -> base -> dark rim),
  // so importance reads at a glance and the graph has some depth. The gradient's dark
  // edge is the rim, so only the centre node still gets an explicit accent ring.
  for(let i=0;i<nodes.length;i++){const n=nodes[i]; if(!passTag(n))continue;
    const r=radius(n), col=n.color||"#888";
    ctx.globalAlpha=faded(n)?0.12:1;
    if(col.length===7){
      if(n._c0===undefined){n._c0=litOf(col); n._c1=drkOf(col);}
      const gr=ctx.createRadialGradient(n.x-r*0.35,n.y-r*0.38,r*0.12, n.x,n.y,r);
      gr.addColorStop(0,n._c0); gr.addColorStop(0.5,col); gr.addColorStop(1,n._c1);
      ctx.fillStyle=gr;
    } else { ctx.fillStyle=col; }
    ctx.beginPath(); ctx.arc(n.x,n.y,r,0,6.283185307); ctx.fill();
    if(n.center||n.id===focusRep){ctx.lineWidth=3; ctx.strokeStyle=theme.accent; ctx.stroke();}
  }
  ctx.globalAlpha=1;
  // Labels only for the centre and the hovered node — never 3500 texts. Drawn in
  // screen space (transform reset) so they stay readable at any zoom.
  ctx.setTransform(dpr,0,0,dpr,0,0);
  drawLabel(centerId);
  if(hoverId&&hoverId!==centerId)drawLabel(hoverId);
  if(focusRep&&focusRep!==centerId&&focusRep!==hoverId)drawLabel(focusRep);
}
function drawLabel(id){ if(!id)return; const n=byId[id]; if(!n||!passTag(n))return;
  const sx=n.x*scale+tx, sy=n.y*scale+ty, r=radius(n)*scale;
  ctx.font="12px system-ui,sans-serif"; ctx.textBaseline="middle";
  ctx.lineWidth=3; ctx.lineJoin="round";
  const t=label(n), x=sx+r+3, y=sy;
  ctx.strokeStyle=theme.halo; ctx.strokeText(t,x,y);   // halo for legibility
  ctx.fillStyle=theme.fg; ctx.fillText(t,x,y);
}

function setGraph(payload){
  tagFilter=[]; searchQ=""; hoverId=null;
  tipHide(); for(const k in tipCache)delete tipCache[k];   // drop stale tooltips
  const prev={}; nodes.forEach(n=>prev[n.id]=n);
  const arr=payload.nodes||[];
  // Community-clustered, deterministic seed. Sort by id so the layout is reproducible
  // regardless of the host's file-walk order; give each colour its own centre via
  // sunflower (phyllotaxis) packing so communities start well-separated; seed each node
  // in a small local cluster around its centre. The layout then STARTS grouped and gravity
  // only has to tighten it -- reliable grouping instead of seed-luck.
  const GOLDEN=Math.PI*(3-Math.sqrt(5));
  const sorted=arr.slice().sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
  // order communities by their hub's degree, descending, so the most important one
  // (MainWindow's) seeds at the sunflower's centre; tie-break by colour for determinism.
  const colDeg={};
  sorted.forEach(n=>{if(!(n.color in colDeg)||n.degree>colDeg[n.color])colDeg[n.color]=n.degree;});
  const colours=[...new Set(sorted.map(n=>n.color))].sort((a,b)=>colDeg[b]-colDeg[a]||(a<b?-1:1));
  topColor=colours[0]||null;   // the most important community -> anchored to centre in step()
  const nc=Math.max(1,colours.length), seedR=80*Math.sqrt(Math.max(1,sorted.length));
  const cc={};
  colours.forEach((c,i)=>{const rr=seedR*1.2*Math.sqrt((i+0.5)/nc), aa=i*GOLDEN;
    cc[c]=[cx+Math.cos(aa)*rr, cy+Math.sin(aa)*rr];});
  const size={}; sorted.forEach(n=>size[n.color]=(size[n.color]||0)+1);
  const seen={};
  nodes=sorted.map(n=>{
    const p=prev[n.id]||{}, c=cc[n.color], k=(seen[n.color]=(seen[n.color]||0)+1);
    // Local radius from the community's SHARE of the layout, not its absolute size:
    // seedR*0.10*sqrt(k) grew with N while the centre spacing shrinks with sqrt(1/nc), so a
    // big community's seed spilled across its neighbours -- reproducible mixing, not grouping
    // (ZT1). Scaling by share keeps each cluster inside its centre's territory.
    const Rc=seedR*1.2*0.55*Math.sqrt(size[n.color]/sorted.length);
    const aa=k*GOLDEN, rr=Rc*Math.sqrt(k/size[n.color]);
    return Object.assign({},n,{
      x:p.x!==undefined?p.x:c[0]+Math.cos(aa)*rr,
      y:p.y!==undefined?p.y:c[1]+Math.sin(aa)*rr, vx:0,vy:0});
  });
  byId={}; nodes.forEach(n=>byId[n.id]=n);
  centerId=null; nodes.forEach(n=>{if(n.center)centerId=n.id;});
  links=(payload.edges||[]).filter(e=>byId[e.source]&&byId[e.target]);
  adj={}; nodes.forEach(n=>adj[n.id]=new Set());
  links.forEach(e=>{adj[e.source].add(e.target);adj[e.target].add(e.source);});
  buildLegend(payload.legend);
  empty.style.display=(nodes.length<=1&&links.length===0)?"flex":"none";
  // Start small & centered, then zoom *in* to the framing once the layout settles.
  setView(frameOf(nodes,0.45)); draw();
  alpha=1; fitPending=true; kick();
}

// The legend is decided host-side (community names, or vault names on the fallback)
// and handed over as [{label,color}] -- render it as-is, textContent for the label.
function buildLegend(items){
  items=items||[];
  legendHd.textContent=strings.legend+" ("+items.length+")";   // ::before is a pseudo-element
  legendItems.textContent=""; focusColor=null;
  items.forEach(it=>{
    const row=document.createElement("div"); row.className="row";
    const i=document.createElement("i"); i.style.background=it.color;
    row.appendChild(i); row.appendChild(document.createTextNode(it.label));
    row.addEventListener("click",()=>focusCommunity(it.color,it.id,row));
    legendItems.appendChild(row);
  });
  legend.style.display = items.length ? "" : "none";   // nothing to show -> hide the box
}
// Click a legend entry -> focus that community: dim every other colour and zoom to it.
// Click the same entry again (or a different one) -> release / switch.
// The community's representative (highest-degree node) is decided host-side and shipped
// in the legend entry's id, so that rule lives ONCE (Python) instead of a JS copy whose
// string ordering would diverge from Python's on non-BMP characters (e.g. emoji in a name).
function focusCommunity(color,repId,row){
  const on = focusColor!==color;
  focusColor = on ? color : null;
  focusRep = on ? (repId||null) : null;
  for(const r of legendItems.children) r.classList.remove("active");
  if(on){ row.classList.add("active"); draw(); zoomTo(nodes.filter(n=>n.color===color)); }
  else { draw(); }
}

// --- Barnes-Hut n-body repulsion ---------------------------------------
// A uniform-grid cutoff only repels within one cell, so detached clusters can
// never push far apart and the layout collapses to a uniform disc. Barnes-Hut
// approximates ALL-pairs repulsion in O(N log N) with a quadtree: near nodes sum
// exactly, a far-enough cell is treated as one mass at its centre. That long-range
// push is what lets hubs splay out and loose clusters drift into their own
// satellites — the readable, structured layout.
function qcell(bx,by,bw){return {mass:0,cmx:0,cmy:0,bx:bx,by:by,bw:bw,body:null,kids:null};}
function qinsert(q,n){
  const m=q.mass;
  q.cmx=(q.cmx*m+n.x)/(m+1); q.cmy=(q.cmy*m+n.y)/(m+1); q.mass=m+1;
  if(m===0){q.body=n;return;}                 // was empty -> becomes a leaf
  if(q.kids===null){                          // was a leaf -> subdivide (unless tiny)
    if(q.bw<1)return;                          // coincident pile: keep as an aggregate leaf
    q.kids=[null,null,null,null];
    const b=q.body; q.body=null; qchild(q,b);
  }
  qchild(q,n);
}
function qchild(q,n){
  const h=q.bw/2, e=n.x>=q.bx+h?1:0, s=n.y>=q.by+h?1:0, i=s*2+e;
  if(!q.kids[i])q.kids[i]=qcell(q.bx+e*h,q.by+s*h,h);
  qinsert(q.kids[i],n);
}
function qrepel(q,n,REP,THETA2,EPS){
  if(q.mass===0)return;
  const dx=q.cmx-n.x, dy=q.cmy-n.y, d2=dx*dx+dy*dy, leaf=q.kids===null;
  let m=q.mass;
  if(leaf){
    // Aggregate leaf: several near-coincident bodies share one cell. Subtract n's own
    // unit of mass when n is one of them -- skipping only q.body would give the stored
    // node nothing while its pile-mates get mass*, a one-sided kick that drifts the pile
    // instead of separating it (ZQ1). Also subsumes the plain single-body self-skip.
    if(n.x>=q.bx && n.x<q.bx+q.bw && n.y>=q.by && n.y<q.by+q.bw) m-=1;
    if(m<=0)return;
  }
  if(leaf || q.bw*q.bw < THETA2*d2){           // leaf, or a cell far enough -> one point mass
    // Direction from the TRUE distance, magnitude from the clamped one: clamping d2 but
    // dividing by sqrt(clamped) shrinks the unit vector below EPS, so repulsion would
    // collapse to zero exactly where near-coincident nodes must push apart (ZP1).
    const dr=Math.sqrt(d2), cd2=d2<EPS?EPS:d2, f=REP*m/cd2;
    if(dr>1e-9){ n.vx-=dx/dr*f; n.vy-=dy/dr*f; }
    return;
  }
  for(let k=0;k<4;k++)if(q.kids[k])qrepel(q.kids[k],n,REP,THETA2,EPS);
}
function step(){
  const L=110, VMAX=300, REP=11000, THETA2=0.81, EPS=9;
  const N=nodes.length; if(N<2){alpha*=0.98;return;}
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  for(let i=0;i<N;i++){const n=nodes[i];
    if(n.x<x0)x0=n.x; if(n.y<y0)y0=n.y; if(n.x>x1)x1=n.x; if(n.y>y1)y1=n.y;}
  const root=qcell(x0,y0,Math.max(x1-x0,y1-y0,1)*1.02);
  for(let i=0;i<N;i++)qinsert(root,nodes[i]);
  for(let i=0;i<N;i++)qrepel(root,nodes[i],REP,THETA2,EPS);
  // springs pull linked nodes toward rest length L
  for(let i=0;i<links.length;i++){const e=links[i],a=byId[e.source],b=byId[e.target];
    let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||0.01,f=(d-L)*0.02;
    dx/=d;dy/=d; a.vx+=dx*f;a.vy+=dy*f; b.vx-=dx*f;b.vy-=dy*f;}
  // Community gravity: pull each node toward the centroid of its colour group, so
  // same-community (same-colour) nodes cluster spatially instead of scattering — the
  // general repulsion still keeps the groups apart. The node's colour IS the group key
  // (in vault-fallback mode this groups by vault, still sane).
  const cen={};
  for(let i=0;i<N;i++){const n=nodes[i]; let c=cen[n.color];
    if(!c){c={x:0,y:0,k:0}; cen[n.color]=c;} c.x+=n.x; c.y+=n.y; c.k++;}
  for(const key in cen){const c=cen[key]; c.x/=c.k; c.y/=c.k;}
  // Centering is only a WEAK anchor: Barnes-Hut repulsion does the spreading, so the
  // strong centering that crushed the structure into a disc is gone.
  const G=0.004, CG=0.06, ANCHOR=0.02, top=topColor?cen[topColor]:null;
  for(let i=0;i<N;i++){const n=nodes[i];
    if(n.id===centerId){n.x=cx;n.y=cy;n.vx=0;n.vy=0;continue;}
    n.vx+=(cx-n.x)*G; n.vy+=(cy-n.y)*G;
    const cg=cen[n.color]; if(cg){n.vx+=(cg.x-n.x)*CG; n.vy+=(cg.y-n.y)*CG;}
    // Anchor the most important community's whole blob toward the centre: a uniform pull
    // by its centroid's offset moves it there without distorting the blob's shape.
    if(top&&n.color===topColor){n.vx+=(cx-top.x)*ANCHOR; n.vy+=(cy-top.y)*ANCHOR;}
    if(n.fixed)continue;
    n.vx*=0.85; n.vy*=0.85;
    // Clamp speed so no node teleports and the fit can't be blown up by a fling.
    const sp=Math.sqrt(n.vx*n.vx+n.vy*n.vy);
    if(sp>VMAX){const k=VMAX/sp; n.vx*=k; n.vy*=k;}
    n.x+=n.vx*alpha; n.y+=n.vy*alpha;}
  alpha*=0.98;
}
function kick(){ if(raf)return; const loop=()=>{
  // Several physics steps per drawn frame while the layout is hot: rendering is
  // cheap now (canvas), so the settle is physics-bound — burning 3 steps per frame
  // converges in roughly a third of the wall-clock, with no visible cost.
  const iters=alpha>0.3?3:1;
  for(let k=0;k<iters;k++)step();
  draw();
  if(alpha>0.02){raf=requestAnimationFrame(loop);}
  else {raf=0; if(fitPending){fitPending=false; zoomTo(nodes);}} };
  raf=requestAnimationFrame(loop);}

// Messages are "verb\tpath[\tcolour]"; the host (parse_graph_message) routes by verb.
function send(msg){ if(window.webkit&&webkit.messageHandlers&&webkit.messageHandlers.graph)
  webkit.messageHandlers.graph.postMessage(msg); }
function clickMsg(n,newTab){ return (newTab?"clicknt":"click")+"\t"+n.id+"\t"+(n.color||""); }

// --- Interaction: one set of handlers on the canvas, manual hit-testing --
function nodeAt(sx,sy){  // screen px -> topmost node under the pointer, or null
  const wx=(sx-tx)/scale, wy=(sy-ty)/scale;
  let hit=null,best=Infinity;
  for(let i=nodes.length-1;i>=0;i--){const n=nodes[i]; if(!passTag(n))continue;
    const r=radius(n)+2, dx=n.x-wx, dy=n.y-wy, d2=dx*dx+dy*dy;
    if(d2<=r*r && d2<best){best=d2;hit=n;}}
  return hit;
}
// Highlight is debounced off the raw pointer: slow to commit on enter (300ms), slower
// to release on leave (1s), so moving off a node doesn't instantly wipe what you were
// studying and a quick glide across the graph doesn't strobe every node it crosses.
function scheduleHover(id){
  if(hoverTimer){clearTimeout(hoverTimer);hoverTimer=0;}
  if(id===hoverId)return;                 // back on the same node -> keep it, cancel any release

  hoverTimer=setTimeout(()=>{ hoverTimer=0; hoverId=id; draw();
    if(id)tipEnter(id); else tipHide(); }, id?300:1000);
}
let down=false,moved=false,sx0=0,sy0=0,lx=0,ly=0,btn=0,dragNode=null,pan=false;
cv.addEventListener("pointerdown",ev=>{
  down=true;moved=false;btn=ev.button;sx0=ev.clientX;sy0=ev.clientY;lx=ev.clientX;ly=ev.clientY;
  zStop();tipHide(); if(hoverTimer){clearTimeout(hoverTimer);hoverTimer=0;}
  if(ev.button===1)ev.preventDefault();  // no middle-click autoscroll
  dragNode=nodeAt(ev.clientX,ev.clientY);
  if(dragNode){dragNode.fixed=true;}
  else{pan=true;cv.classList.add("grabbing");}
  try{cv.setPointerCapture(ev.pointerId);}catch(e){}
});
cv.addEventListener("pointermove",ev=>{
  if(down){
    if(Math.abs(ev.clientX-sx0)+Math.abs(ev.clientY-sy0)>3)moved=true;
    // clientX/Y deltas, not ev.movementX: movementX is device-scaled/unreliable in
    // WebKit, so the graph would lag the cursor. clientX deltas are CSS px -> 1:1 pan.
    const dx=ev.clientX-lx, dy=ev.clientY-ly; lx=ev.clientX; ly=ev.clientY;
    if(dragNode){dragNode.x+=dx/scale;dragNode.y+=dy/scale;draw();}
    else if(pan){tx+=dx;ty+=dy;draw();}
    return;
  }
  const h=nodeAt(ev.clientX,ev.clientY), id=h?h.id:null;
  if(id!==hoverTarget){hoverTarget=id; scheduleHover(id);}
});
cv.addEventListener("pointerup",ev=>{
  if(!down)return; down=false;
  try{cv.releasePointerCapture(ev.pointerId);}catch(e){}
  if(dragNode){dragNode.fixed=false;
    // Detect click vs. drag: a no-move release IS the click (middle / Ctrl -> new tab).
    // The host decides what a single click means (collect a card, or open); a
    // double click always opens and is handled by the dblclick listener below.
    if(!moved)send(clickMsg(dragNode, btn===1||ev.ctrlKey));
    else{alpha=Math.max(alpha,0.4);kick();}
    dragNode=null;}
  else if(pan){pan=false;cv.classList.remove("grabbing");}
});
cv.addEventListener("dblclick",ev=>{const h=nodeAt(ev.clientX,ev.clientY);
  if(h)send("dblclick\t"+h.id);});
cv.addEventListener("pointerleave",()=>{
  if(!down){hoverTarget=null;scheduleHover(null);} });
cv.addEventListener("wheel",ev=>{ev.preventDefault();zStop();tipHide();
  if(hoverTimer){clearTimeout(hoverTimer);hoverTimer=0;}
  const f=ev.deltaY<0?1.1:0.9,mx=ev.clientX,my=ev.clientY;
  tx=mx-(mx-tx)*f;ty=my-(my-ty)*f;scale*=f;draw();},{passive:false});

function zStop(){ if(zraf){cancelAnimationFrame(zraf);zraf=0;} }  // cancel on user interaction
function animateTo(s,x,y){
  zStop();
  const s0=scale,x0=tx,y0=ty, ds=s-s0,dx=x-x0,dy=y-y0, dur=380;
  if(Math.abs(ds)<1e-4&&Math.abs(dx)<0.5&&Math.abs(dy)<0.5){scale=s;tx=x;ty=y;draw();return;}
  let start=null;
  const ease=t=>t<0.5?2*t*t:1-Math.pow(-2*t+2,2)/2;  // ease-in-out
  const loop=now=>{ if(start===null)start=now; const k=Math.min(1,(now-start)/dur),e=ease(k);
    scale=s0+ds*e; tx=x0+dx*e; ty=y0+dy*e; draw();
    zraf = k<1 ? requestAnimationFrame(loop) : 0; };
  zraf=requestAnimationFrame(loop);
}
function frameOf(list,factor){  // [scale,tx,ty] to frame `list` at factor×fit, or null
  if(!list.length)return null;
  // Robust extent: frame the 2nd–98th percentile of x/y, so a handful of flung-out
  // nodes don't blow up the bounding box and zoom the bulk into an invisible speck.
  const xs=list.map(n=>n.x).filter(isFinite).sort((p,q)=>p-q);
  const ys=list.map(n=>n.y).filter(isFinite).sort((p,q)=>p-q);
  if(!xs.length||!ys.length)return null;  // never blank the view
  const pick=(arr,t)=>arr[Math.min(arr.length-1,Math.max(0,Math.round(t*(arr.length-1))))];
  const a=pick(xs,0.02),c=pick(xs,0.98),b=pick(ys,0.02),d=pick(ys,0.98);
  const pad=60,bw=(c-a)+pad*2,bh=(d-b)+pad*2;
  // Floor low enough to frame a wide layout whole; bounded centering keeps the
  // usual fit well above it, so 0.05 is a safety net, not the common case.
  const s=Math.min(2.5,Math.max(0.05,Math.min(W/bw,H/bh)))*(factor||1);
  return [s, W/2-((a+c)/2)*s, H/2-((b+d)/2)*s];
}
function setView(v){ if(v){zStop();scale=v[0];tx=v[1];ty=v[2];draw();} }  // instant
function zoomTo(list){ const v=frameOf(list,1); if(v)animateTo(v[0],v[1],v[2]); }
function fit(){zoomTo(nodes);}
function setTagFilter(tags){tagFilter=tags||[];draw();}
function search(q){searchQ=(q||"").toLowerCase();draw();
  if(searchQ){const hits=nodes.filter(n=>passTag(n)&&matchSearch(n));zoomTo(hits);}}

// --- Hover node-info panel (fixed top-right corner) ----------------------
function tipEnter(id){
  tipHide();                       // cancel any pending/shown tip first
  tipFor=id;
  tipTimer=setTimeout(()=>{tipTimer=0; tipRequest(id);}, 500);
}
function tipHide(){
  if(tipTimer){clearTimeout(tipTimer);tipTimer=0;}
  tipFor=null; tip.style.display="none";
}
function tipRequest(id){
  // Stale-while-revalidate: show any cached text at once (no flicker), but always
  // re-ask the host — the file may have changed since it was last cached.
  if(tipCache[id]!==undefined)tipShow(id);
  send("tip\t"+id);
}
function tipShow(id){
  const data=tipCache[id];
  if(!data||tipFor!==id)return;    // moved off the node before it resolved
  tip.textContent="";
  const t=document.createElement("div"); t.className="tip-title";
  t.textContent=data.title; tip.appendChild(t);   // textContent: body is untrusted
  if(data.desc){const d=document.createElement("div"); d.className="tip-desc";
    d.textContent=data.desc; tip.appendChild(d);}
  const node=byId[id];
  if(node&&node.vault){const v=document.createElement("div"); v.className="tip-vault";
    const dot=document.createElement("i"); dot.style.background=node.color||"#888";
    v.appendChild(dot); v.appendChild(document.createTextNode(base(node.vault)));
    tip.appendChild(v);}
  tip.style.display="block";   // fixed top-right corner — no pointer anchoring
}
// Host resolves (title, description) lazily and calls this back.
window.showTip=function(id,title,desc){tipCache[id]={title:title,desc:desc};
  if(tipFor===id)tipShow(id);};

// Apply theme colours pushed from the host (GTK style context): mirror them into
// CSS variables (for the HTML tooltip/legend) AND the `theme` object (for the canvas).
window.setTheme=function(vars){const s=document.documentElement.style;
  for(const k in vars)s.setProperty(k,vars[k]);
  if(vars["--edge"])theme.edge=vars["--edge"];
  if(vars["--edge-out"])theme.edgeOut=vars["--edge-out"];
  if(vars["--node-ring"])theme.nodeRing=vars["--node-ring"];
  if(vars["--accent"])theme.accent=vars["--accent"];
  if(vars["--fg"])theme.fg=vars["--fg"];
  if(vars["--halo"])theme.halo=vars["--halo"];
  draw();
};

// Host-injected, gettext-translated chrome strings (the WebView can't call gettext).
window.setStrings=function(s){Object.assign(strings,s); empty.textContent=strings.noConnections;};

window.setGraph=setGraph; window.setTagFilter=setTagFilter; window.search=search; window.fit=fit;
resize();
</script></body></html>
"""


def parse_graph_message(raw):
    r"""Parse a raw ``verb\tpath[\tcolor]`` message from the graph page.

    Returns ``(kind, path, arg)``: kind is one of ``"tip"``, ``"click"``,
    ``"clicknt"``, ``"dblclick"``, or ``""`` for an empty or unrecognised message.
    ``arg`` carries the node colour for the click verbs and ``""`` otherwise.
    """
    verb, _, rest = raw.partition("\t")
    if verb in ("tip", "dblclick"):
        return (verb, rest, "")
    if verb in ("click", "clicknt"):
        # The colour is always the LAST field and never contains a tab, so split from
        # the RIGHT — a note filename may legally contain a tab (POSIX allows any byte
        # but "/" and NUL), and a left split would truncate the path.
        path, sep, arg = rest.rpartition("\t")
        return (verb, path, arg) if sep else (verb, rest, "")
    return ("", "", "")


class GraphView(Gtk.Box):
    __gsignals__ = {
        # node-activated(file_path): open the file — a double click in collect mode,
        # a single click otherwise.
        "node-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # node-activated-new-tab(file_path): middle-click / Ctrl+click on a node.
        "node-activated-new-tab": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # node-carded(file_path, colour): a single click in collect mode — collect
        # the node as a card rather than opening it.
        "node-carded": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
    }

    def __init__(self, collect_on_click: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        # collect_on_click flips a single click from "open the file" (default, the
        # sidebar mini-graph) to "collect a card" (the main explorer). A double click
        # opens in either mode.
        self._collect_on_click = collect_on_click
        self._web = WebKit.WebView()
        self._web.set_vexpand(True)
        self._web.set_hexpand(True)
        try:
            self._web.set_background_color(_transparent())
        except Exception:
            # Expected cosmetic fallback: some WebKit builds lack the setter, and a
            # non-transparent graph background is only a minor visual regression.
            logger.debug("could not set the graph WebView background transparent",
                         exc_info=True)
        settings = self._web.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(False)

        ucm = self._web.get_user_content_manager()
        ucm.register_script_message_handler("graph", None)
        ucm.connect("script-message-received::graph", self._on_message)

        self._ready = False
        self._pending = None
        self._web.connect("load-changed", self._on_load_changed)
        self._web.load_html(_PAGE, "about:blank")
        self.append(self._web)

        # Re-theme the page when the app switches light/dark or changes accent.
        self._style = Adw.StyleManager.get_default()
        self._style_ids = [self._style.connect("notify::dark", self._on_theme_changed)]
        if self._style.find_property("accent-color") is not None:
            self._style_ids.append(
                self._style.connect("notify::accent-color", self._on_theme_changed))

    def set_graph(self, payload: dict) -> None:
        """Render *payload* ({"nodes": [...], "edges": [...]}); safe before load."""
        self._pending = payload
        if self._ready:
            self._push(payload)

    def search(self, query: str) -> None:
        """Highlight nodes matching *query* and zoom to fit them."""
        self._eval("window.search(%s);" % json.dumps(query or ""))

    def set_tag_filter(self, tags) -> None:
        """Show only nodes carrying one of *tags* (empty list = show all)."""
        self._eval("window.setTagFilter(%s);" % json.dumps(list(tags or [])))

    def fit(self) -> None:
        """Zoom/pan so the whole graph fits the viewport."""
        self._eval("window.fit();")

    def _push(self, payload: dict) -> None:
        self._eval("window.setGraph(%s);" % json.dumps(payload))

    def _eval(self, js: str) -> None:
        if self._ready:
            self._web.evaluate_javascript(js, -1, None, None, None, None, None)

    def _on_load_changed(self, _web, event) -> None:
        if event == WebKit.LoadEvent.FINISHED:
            self._ready = True
            self._apply_theme()          # theme the first paint, before any graph
            self._apply_strings()        # translated chrome strings, before any legend
            if self._pending is not None:
                self._push(self._pending)

    def _on_theme_changed(self, *_a) -> None:
        # Defer so the style context has settled on the new theme before we read it.
        GLib.idle_add(self._apply_theme)

    def _apply_theme(self) -> None:
        """The graph CANVAS is a fixed dark surface (the page background is black), so
        its colours deliberately do NOT follow the light/dark GTK theme — a themed light
        foreground on black would be unreadable. The LEGEND box, by contrast, follows the
        system theme so it reads as a native panel. Only the accent (centre/focus ring,
        out-edges) follows the user's accent colour."""
        if not self._ready:
            return
        ctx = self._web.get_style_context()

        def look(*names):
            for n in names:
                ok, c = ctx.lookup_color(n)
                if ok:
                    return c
            return None

        def css(c, alpha=None):
            return "rgba(%d,%d,%d,%g)" % (
                round(c.red * 255), round(c.green * 255), round(c.blue * 255),
                c.alpha if alpha is None else alpha)

        accent = look("accent_color", "accent_bg_color")
        leg_bg = look("popover_bg_color", "window_bg_color")   # legend = native panel
        leg_fg = look("popover_fg_color", "window_fg_color")

        v = {
            "--fg": "rgba(232,234,240,0.92)",       # labels
            "--dim": "rgba(232,234,240,0.5)",       # empty-state text
            "--edge": "rgba(200,206,220,0.34)",     # links, a touch brighter to read on black
            "--node-ring": "rgba(0,0,0,0.35)",
            "--halo": "rgba(0,0,0,0.7)",            # text outline: dark, so light labels pop
            "--accent": css(accent) if accent else "#e66100",
            "--edge-out": css(accent, 0.85) if accent else "rgba(230,97,0,0.85)",
        }
        # The legend box AND the hover tooltip are chrome, not part of the dark canvas,
        # so both follow the system popover colours (falling back to Canvas/CanvasText
        # in CSS when no theme colour resolves).
        if leg_bg is not None:
            v["--legend-bg"] = v["--tip-bg"] = css(leg_bg, 0.96)
        if leg_fg is not None:
            v["--legend-fg"] = v["--tip-fg"] = css(leg_fg)
            # panel outline stays subtle; the colour-dot ring needs a stronger alpha (ZX1)
            v["--legend-border"] = v["--tip-border"] = css(leg_fg, 0.22)
            v["--swatch-ring"] = css(leg_fg, 0.45)
        self._eval("window.setTheme(%s);" % json.dumps(v))

    def _apply_strings(self) -> None:
        """Hand the gettext-translated chrome strings to the page — the WebView cannot
        call gettext itself, so the (few) UI labels are injected from the host."""
        self._eval("window.setStrings(%s);" % json.dumps({
            "legend": _("Legend"),
            "noConnections": _("no connections"),
        }))

    def _on_message(self, _ucm, js_value) -> None:
        try:
            raw = js_value.to_string()
        except Exception:
            # The JS return channel: if this breaks, a node click is dropped and
            # nothing happens on screen. Leave a trace instead of failing silently.
            logger.warning("graph JS message could not be read; a click was dropped",
                           exc_info=True)
            return
        kind, path, color = parse_graph_message(raw)
        if not kind or not path:
            return
        if kind == "tip":      # lazy hover-panel request for node `path`
            self._send_tip(path)
            return
        if self._collect_on_click:
            # Main explorer: a single click collects a card, a double click opens.
            if kind == "click":
                self.emit("node-carded", path, color)
            elif kind == "clicknt":
                self.emit("node-carded", path, color)
                self.emit("node-activated-new-tab", path)
            elif kind == "dblclick":
                self.emit("node-activated", path)
        else:
            # Default (sidebar mini-graph): a single click opens, as before.
            if kind == "click":
                self.emit("node-activated", path)
            elif kind == "clicknt":
                self.emit("node-activated-new-tab", path)
            # dblclick ignored here: the single click already opened.

    def _send_tip(self, path: str) -> None:
        """Resolve a node's hover tooltip (title/description or a body preview)
        and hand it back to the page, which caches it per node."""
        title, desc = frontmatter.tip_of(path)
        self._eval("window.showTip(%s, %s, %s);"
                   % (json.dumps(path), json.dumps(title), json.dumps(desc)))

    def teardown(self) -> None:
        """Release the WebView (frees the WebKit web process)."""
        try:
            for hid in self._style_ids:
                self._style.disconnect(hid)
            self._style_ids = []
        except Exception:      # noqa: BLE001 — teardown: the view is being released,
            pass               # a disconnect on an already-finalised handler is harmless
        try:
            self._web.try_close()
        except Exception:      # noqa: BLE001 — teardown: nothing to recover if the web
            pass               # process is already gone


def _transparent():
    from gi.repository import Gdk
    rgba = Gdk.RGBA()
    rgba.parse("rgba(0,0,0,0)")
    return rgba
