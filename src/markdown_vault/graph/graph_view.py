"""Knowledge-graph render component — a WebKit WebView with an inline,
self-contained force-directed layout (no CDN, no external assets).

This is a *separate* WebView from the Markdown preview: it runs its own page
scripts under a permissive-but-local CSP (``script-src 'unsafe-inline'``, no
network), whereas the preview stays ``script-src 'none'``.  Node labels come
from user filenames and are rendered as SVG text (``textContent``), never HTML,
so there is no injection surface.

Usage:
    view = GraphView()
    view.connect("node-activated", lambda _v, path: open_file(path))
    view.set_graph(payload)          # {"nodes": [...], "edges": [...]}
"""

import json
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, WebKit, GObject, GLib, Adw

from markdown_vault.markdown import frontmatter


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<style>
  /* Colours are theme variables injected from the GTK style context (see
     _apply_theme); the literals here are only fallbacks before that runs. */
  html,body{margin:0;height:100%;overflow:hidden;background:transparent;
    font:12px system-ui,sans-serif;color:var(--fg,#888)}
  #svg{width:100%;height:100%;display:block;cursor:grab}
  #svg.grabbing{cursor:grabbing}
  #arrow path{fill:var(--edge-out,#5b9bd5cc)}
  .edge{stroke:var(--edge,#8888);stroke-width:1.3;fill:none}
  .edge.out{stroke:var(--edge-out,#5b9bd5cc);stroke-width:1.6;marker-end:url(#arrow)}
  .node circle{stroke:var(--node-ring,rgba(0,0,0,.35));stroke-width:1;cursor:pointer}
  .node.center circle{stroke:var(--accent,#e66100);stroke-width:3}
  .node text{fill:currentColor;pointer-events:none;paint-order:stroke;
    stroke:var(--halo,rgba(127,127,127,.35));stroke-width:3px}
  .faded{opacity:.12;transition:opacity .1s}
  #legend{position:absolute;left:8px;bottom:6px;font-size:11px;opacity:.85}
  #legend span{margin-right:10px;white-space:nowrap}
  #legend i{display:inline-block;width:9px;height:9px;border-radius:50%;
    margin-right:4px;vertical-align:-1px}
  #empty{position:absolute;inset:0;display:none;align-items:center;
    justify-content:center;color:var(--dim,#999);font-style:italic}
  /* A 0x0 anchor placed at the pointer; the tooltip is positioned against it
     with CSS anchor positioning, so the browser handles viewport-edge flipping. */
  #tipanchor{position:absolute;width:0;height:0;anchor-name:--tip}
  #tip{position:absolute;display:none;pointer-events:none;max-width:280px;
    position-anchor:--tip;top:anchor(bottom);left:anchor(right);
    margin:6px 0 0 6px;position-try-fallbacks:flip-block,flip-inline;
    background:var(--tip-bg,Canvas);color:var(--tip-fg,CanvasText);
    border:1px solid var(--tip-border,rgba(127,127,127,.45));
    border-radius:6px;padding:6px 9px;line-height:1.35;z-index:10;
    box-shadow:0 2px 12px rgba(0,0,0,.45)}
  #tip .tip-title{font-weight:600}
  #tip .tip-desc{opacity:.8;margin-top:2px;white-space:normal;
    overflow-wrap:anywhere}
  #tip .tip-vault{display:flex;align-items:center;gap:5px;opacity:.7;
    font-size:.9em;margin-top:5px}
  #tip .tip-vault i{width:8px;height:8px;border-radius:50%;flex:none}
</style></head>
<body>
<svg id="svg"><defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
    markerHeight="6" orient="auto-start-reverse">
    <path d="M0 0L10 5L0 10z" fill="#5b9bd5cc"/></marker>
</defs><g id="view"><g id="edges"></g><g id="nodes"></g></g></svg>
<div id="legend"></div>
<div id="empty">no connections</div>
<div id="tipanchor"></div>
<div id="tip"></div>
<script>
const SVGNS="http://www.w3.org/2000/svg";
const svg=document.getElementById("svg"), view=document.getElementById("view");
const gEdges=document.getElementById("edges"), gNodes=document.getElementById("nodes");
const legend=document.getElementById("legend"), empty=document.getElementById("empty");
const tip=document.getElementById("tip"), tipAnchor=document.getElementById("tipanchor");
let W=innerWidth,H=innerHeight,cx=W/2,cy=H/2;
let nodes=[],links=[],byId={},centerId=null,adj={};
let tx=0,ty=0,scale=1,alpha=0,raf=0,fitPending=false,zraf=0;
let tagFilter=[],searchQ="",hoverId=null;
// Hover tooltip: 500ms-debounced, lazily resolved by the host and cached per id.
let tipTimer=0,tipFor=null,tipX=0,tipY=0;
const tipCache={};

function radius(n){return 5+Math.min(9,Math.sqrt(n.degree||0)*2)+(n.center?3:0);}

function setGraph(payload){
  tagFilter=[]; searchQ=""; hoverId=null;
  tipHide(); for(const k in tipCache)delete tipCache[k];   // drop stale tooltips
  const prev={}; nodes.forEach(n=>prev[n.id]=n);
  const arr=payload.nodes||[];
  // Scale the seed circle with sqrt(N) so it spans many grid cells (cell=340):
  // a fixed radius bunches every node into one cell, and the first frames of a
  // big graph run full O(N^2) before repulsion scatters them (R30.1). This also
  // gives a calmer opening — nodes no longer start on top of each other.
  const seedR=80*Math.sqrt(Math.max(1,arr.length));
  nodes=arr.map((n,i)=>{
    const p=prev[n.id]||{}, a=2*Math.PI*i/Math.max(1,arr.length);
    return Object.assign({},n,{
      x:p.x!==undefined?p.x:cx+Math.cos(a)*seedR,
      y:p.y!==undefined?p.y:cy+Math.sin(a)*seedR, vx:0,vy:0});
  });
  byId={}; nodes.forEach(n=>byId[n.id]=n);
  centerId=null; nodes.forEach(n=>{if(n.center)centerId=n.id;});
  links=(payload.edges||[]).filter(e=>byId[e.source]&&byId[e.target]);
  adj={}; nodes.forEach(n=>adj[n.id]=new Set());
  links.forEach(e=>{adj[e.source].add(e.target);adj[e.target].add(e.source);});
  buildLegend(nodes);
  empty.style.display=(nodes.length<=1&&links.length===0)?"flex":"none";
  render();
  // Start small & centered, then zoom *in* to the framing once the layout
  // settles (fitPending, consumed in kick()) — a reveal rather than a snap-out.
  setView(frameOf(nodes,0.45));
  alpha=1; fitPending=true; kick();
}

function buildLegend(ns){
  const seen={}; ns.forEach(n=>{seen[n.vault]=n.color;});
  legend.textContent="";
  Object.keys(seen).forEach(v=>{
    const s=document.createElement("span"), i=document.createElement("i");
    i.style.background=seen[v];
    s.appendChild(i); s.appendChild(document.createTextNode(base(v)));
    legend.appendChild(s);
  });
}
function base(p){const q=p.replace(/\/+$/,"").split("/");return q[q.length-1]||p;}
function label(n){return n.label||base(n.id);}

let elNodes={},elEdges=[];
function render(){
  gEdges.textContent=""; gNodes.textContent=""; elNodes={}; elEdges=[];
  links.forEach(e=>{
    const l=document.createElementNS(SVGNS,"line");
    l.setAttribute("class","edge "+(e.source===centerId?"out":""));
    gEdges.appendChild(l); elEdges.push({l,e});
  });
  nodes.forEach(n=>{
    const g=document.createElementNS(SVGNS,"g");
    g.setAttribute("class","node"+(n.center?" center":""));
    const c=document.createElementNS(SVGNS,"circle");
    c.setAttribute("r",radius(n)); c.setAttribute("fill",n.color||"#888");
    const tx=document.createElementNS(SVGNS,"text");
    tx.setAttribute("x",radius(n)+3); tx.setAttribute("y",4);
    tx.textContent=n.center?label(n):"";
    g.appendChild(c); g.appendChild(tx); gNodes.appendChild(g);
    elNodes[n.id]={g,c,tx};
    c.addEventListener("pointerenter",ev=>{highlight(n.id);tipEnter(n.id,ev);});
    c.addEventListener("pointermove",ev=>{tipX=ev.clientX;tipY=ev.clientY;});
    c.addEventListener("pointerleave",()=>{highlight(null);tipHide();});
    makeDraggable(g,n);
  });
  positions();
}
function positions(){
  elEdges.forEach(({l,e})=>{const a=byId[e.source],b=byId[e.target];
    l.setAttribute("x1",a.x);l.setAttribute("y1",a.y);
    l.setAttribute("x2",b.x);l.setAttribute("y2",b.y);});
  nodes.forEach(n=>{const el=elNodes[n.id];
    if(el)el.g.setAttribute("transform","translate("+n.x+","+n.y+")");});
  view.setAttribute("transform","translate("+tx+","+ty+") scale("+scale+")");
}
function highlight(id){hoverId=id;applyFilters();}  // hover is one input to fade

function step(){
  const K=6000, L=70, C=340, C2=C*C, INV=1/C;
  // Repulsion is O(N^2) naively. Bucket nodes into a uniform grid of cell size
  // C and only compute repulsion within the 3x3 cell neighbourhood — forces
  // past C are negligible (K/C^2 is <2% of a link's pull). Near O(N) for spread
  // layouts, so the explorer's "All vaults" scope stays responsive on big vaults.
  const grid=new Map();
  for(let i=0;i<nodes.length;i++){const n=nodes[i];
    const key=Math.floor(n.x*INV)+","+Math.floor(n.y*INV);
    let cell=grid.get(key); if(!cell){cell=[];grid.set(key,cell);} cell.push(i);}
  for(let i=0;i<nodes.length;i++){const a=nodes[i];
    const gx=Math.floor(a.x*INV),gy=Math.floor(a.y*INV);
    for(let ox=-1;ox<=1;ox++)for(let oy=-1;oy<=1;oy++){
      const cell=grid.get((gx+ox)+","+(gy+oy)); if(!cell)continue;
      for(let c=0;c<cell.length;c++){const j=cell[c]; if(j<=i)continue;  // each pair once
        const b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy;
        if(d2>C2)continue; if(d2<0.01)d2=0.01;
        // Cap the force: coincident nodes would otherwise get K/0.01=6e5 and be
        // flung off-screen for a frame, blowing up the bounding box (and fit).
        let f=K/d2; if(f>100)f=100; let d=Math.sqrt(d2); dx/=d;dy/=d;
        a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;}
    }
  }
  links.forEach(e=>{const a=byId[e.source],b=byId[e.target];
    let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||0.01,f=(d-L)*0.02;
    dx/=d;dy/=d; a.vx+=dx*f;a.vy+=dy*f; b.vx-=dx*f;b.vy-=dy*f;});
  // Centering scales with sqrt(N) so the settled extent stays bounded without
  // compressing into a hairball: it still curbs runaway equilibrium (R31.1),
  // but lets the extent grow gently with N so node density stays roughly
  // constant (the fit then zooms out instead). Unchanged for small graphs
  // (N<=120, e.g. the sidebar's local graph).
  const G=0.006*Math.max(1,Math.sqrt(nodes.length/120));
  nodes.forEach(n=>{
    if(n.id===centerId){n.x=cx;n.y=cy;n.vx=0;n.vy=0;return;}
    n.vx+=(cx-n.x)*G; n.vy+=(cy-n.y)*G;
    if(n.fixed)return;
    n.vx*=0.85; n.vy*=0.85; n.x+=n.vx*alpha; n.y+=n.vy*alpha;
  });
  alpha*=0.98;
}
function kick(){ if(raf)return; const loop=()=>{ step(); positions();
  if(alpha>0.02){raf=requestAnimationFrame(loop);}
  else {raf=0; if(fitPending){fitPending=false; zoomTo(nodes);}} }; raf=requestAnimationFrame(loop);}

// newTab flag is prefixed ("1\t" / "0\t") so the host can route it.
function post(id,newTab){ if(window.webkit&&webkit.messageHandlers&&webkit.messageHandlers.graph)
  webkit.messageHandlers.graph.postMessage((newTab?"1":"0")+"\t"+id); }
function makeDraggable(g,n){
  // Detect click vs. drag here: pointer capture (needed for dragging) swallows
  // the synthetic click event, so a no-move pointerup IS the click.
  let down=false,moved=false,sx=0,sy=0,btn=0;
  g.addEventListener("pointerdown",ev=>{down=true;moved=false;btn=ev.button;zStop();tipHide();
    if(ev.button===1)ev.preventDefault();  // no middle-click autoscroll
    sx=ev.clientX;sy=ev.clientY;n.fixed=true;
    try{g.setPointerCapture(ev.pointerId);}catch(e){} ev.stopPropagation();});
  g.addEventListener("pointermove",ev=>{if(!down)return;
    if(Math.abs(ev.clientX-sx)+Math.abs(ev.clientY-sy)>3)moved=true;
    n.x+=ev.movementX/scale;n.y+=ev.movementY/scale;positions();});
  g.addEventListener("pointerup",ev=>{if(!down)return;down=false;n.fixed=false;
    try{g.releasePointerCapture(ev.pointerId);}catch(e){}
    if(moved){alpha=Math.max(alpha,0.4);kick();}
    else {post(n.id, btn===1 || ev.ctrlKey);}});  // middle / Ctrl → new tab
}
// pan + zoom on the background
let pan=false,px=0,py=0;
svg.addEventListener("pointerdown",ev=>{pan=true;px=ev.clientX;py=ev.clientY;
  zStop();tipHide();svg.classList.add("grabbing");});
svg.addEventListener("pointermove",ev=>{if(!pan)return;
  tx+=ev.clientX-px;ty+=ev.clientY-py;px=ev.clientX;py=ev.clientY;positions();});
window.addEventListener("pointerup",()=>{pan=false;svg.classList.remove("grabbing");});
svg.addEventListener("wheel",ev=>{ev.preventDefault();zStop();tipHide();
  const f=ev.deltaY<0?1.1:0.9,mx=ev.clientX,my=ev.clientY;
  tx=mx-(mx-tx)*f;ty=my-(my-ty)*f;scale*=f;positions();},{passive:false});
addEventListener("resize",()=>{W=innerWidth;H=innerHeight;cx=W/2;cy=H/2;
  alpha=Math.max(alpha,0.3);fitPending=true;kick();});  // reframe after the relayout
// Tag filter = hard hide (show only nodes carrying a selected tag; empty = all).
// Search = soft dim of non-matching nodes + zoom-to-fit the matches.
function passTag(n){return tagFilter.length===0||(n.tags||[]).some(t=>tagFilter.indexOf(t)>=0);}
function matchSearch(n){return !searchQ||label(n).toLowerCase().indexOf(searchQ)>=0
  ||(n.id||"").toLowerCase().indexOf(searchQ)>=0;}
function nearHover(id){return hoverId===id||(adj[hoverId]&&adj[hoverId].has(id));}
// A node is dimmed if the search excludes it OR a hover excludes it — both
// contribute so leaving a hover restores (not clears) the search dim.
function faded(n){return (searchQ&&!matchSearch(n))||(hoverId&&!nearHover(n.id));}
function applyFilters(){
  const shown=new Set();
  nodes.forEach(n=>{const el=elNodes[n.id];if(!el)return;
    const vis=passTag(n); el.g.style.display=vis?"":"none";
    el.g.classList.toggle("faded", vis&&!!faded(n));
    if(vis)shown.add(n.id);});
  elEdges.forEach(({l,e})=>{const both=shown.has(e.source)&&shown.has(e.target);
    l.style.display=both?"":"none";
    l.classList.toggle("faded", both&&(faded(byId[e.source])||faded(byId[e.target])));});
  empty.style.display=(shown.size===0)?"flex":"none";
}
function zStop(){ if(zraf){cancelAnimationFrame(zraf);zraf=0;} }  // cancel on user interaction
function animateTo(s,x,y){
  zStop();
  const s0=scale,x0=tx,y0=ty, ds=s-s0,dx=x-x0,dy=y-y0, dur=380;
  if(Math.abs(ds)<1e-4&&Math.abs(dx)<0.5&&Math.abs(dy)<0.5){scale=s;tx=x;ty=y;positions();return;}
  let start=null;
  const ease=t=>t<0.5?2*t*t:1-Math.pow(-2*t+2,2)/2;  // ease-in-out
  const loop=now=>{ if(start===null)start=now; const k=Math.min(1,(now-start)/dur),e=ease(k);
    scale=s0+ds*e; tx=x0+dx*e; ty=y0+dy*e; positions();
    zraf = k<1 ? requestAnimationFrame(loop) : 0; };
  zraf=requestAnimationFrame(loop);
}
function frameOf(list,factor){  // [scale,tx,ty] to frame `list` at factor×fit, or null
  if(!list.length)return null;
  let a=1e9,b=1e9,c=-1e9,d=-1e9;
  list.forEach(n=>{a=Math.min(a,n.x);b=Math.min(b,n.y);c=Math.max(c,n.x);d=Math.max(d,n.y);});
  if(!(isFinite(a)&&isFinite(b)&&isFinite(c)&&isFinite(d)))return null;  // never blank the view
  const pad=60,bw=(c-a)+pad*2,bh=(d-b)+pad*2;
  // Floor low enough to frame a wide layout whole; bounded centering keeps the
  // usual fit well above it, so 0.05 is a safety net, not the common case.
  const s=Math.min(2.5,Math.max(0.05,Math.min(W/bw,H/bh)))*(factor||1);
  return [s, W/2-((a+c)/2)*s, H/2-((b+d)/2)*s];
}
function setView(v){ if(v){zStop();scale=v[0];tx=v[1];ty=v[2];positions();} }  // instant
function zoomTo(list){ const v=frameOf(list,1); if(v)animateTo(v[0],v[1],v[2]); }
function fit(){zoomTo(nodes);}
function setTagFilter(tags){tagFilter=tags||[];applyFilters();}
function search(q){searchQ=(q||"").toLowerCase();applyFilters();
  if(searchQ){const hits=nodes.filter(n=>passTag(n)&&matchSearch(n));zoomTo(hits);}}
// --- Hover tooltip -------------------------------------------------------
function tipEnter(id,ev){
  tipHide();                       // cancel any pending/shown tip first
  tipFor=id; tipX=ev.clientX; tipY=ev.clientY;
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
  if(window.webkit&&webkit.messageHandlers&&webkit.messageHandlers.graph)
    webkit.messageHandlers.graph.postMessage("tip\t"+id);
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
  // Park the anchor at the pointer; CSS anchor positioning places the tooltip
  // and flips it at the viewport edges (position-try-fallbacks).
  tipAnchor.style.left=tipX+"px"; tipAnchor.style.top=tipY+"px";
  tip.style.display="block";
}
// Host resolves (title, description) lazily and calls this back.
window.showTip=function(id,title,desc){tipCache[id]={title:title,desc:desc};
  if(tipFor===id)tipShow(id);};

// Apply theme colours pushed from the host (GTK style context) as CSS variables.
window.setTheme=function(vars){const s=document.documentElement.style;
  for(const k in vars)s.setProperty(k,vars[k]);};

window.setGraph=setGraph; window.setTagFilter=setTagFilter; window.search=search; window.fit=fit;
</script></body></html>
"""


class GraphView(Gtk.Box):
    __gsignals__ = {
        # node-activated(file_path): a plain click on a node.
        "node-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # node-activated-new-tab(file_path): middle-click / Ctrl+click on a node.
        "node-activated-new-tab": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._web = WebKit.WebView()
        self._web.set_vexpand(True)
        self._web.set_hexpand(True)
        try:
            self._web.set_background_color(_transparent())
        except Exception:
            pass
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
            if self._pending is not None:
                self._push(self._pending)

    def _on_theme_changed(self, *_a) -> None:
        # Defer so the style context has settled on the new theme before we read it.
        GLib.idle_add(self._apply_theme)

    def _apply_theme(self) -> None:
        """Pull the relevant colours from the GTK style context and hand them to
        the page as CSS variables, so the graph follows the Adwaita theme (light/
        dark, accent) instead of hardcoding its own palette."""
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

        fg = look("window_fg_color", "view_fg_color")
        bg = look("window_bg_color", "view_bg_color")
        accent = look("accent_color", "accent_bg_color")
        pop_bg = look("popover_bg_color", "window_bg_color")
        pop_fg = look("popover_fg_color", "window_fg_color")

        v = {}
        if fg is not None:
            v["--fg"] = css(fg)
            v["--dim"] = css(fg, 0.55)
            v["--edge"] = css(fg, 0.28)
            v["--node-ring"] = css(fg, 0.35)
            v["--tip-border"] = css(fg, 0.20)
        if bg is not None:
            v["--halo"] = css(bg, 0.55)          # text outline = background, for legibility
        if accent is not None:
            v["--accent"] = css(accent)
            v["--edge-out"] = css(accent, 0.85)
        if pop_bg is not None:
            v["--tip-bg"] = css(pop_bg)
        if pop_fg is not None:
            v["--tip-fg"] = css(pop_fg)
        if v:
            self._eval("window.setTheme(%s);" % json.dumps(v))

    def _on_message(self, _ucm, js_value) -> None:
        try:
            raw = js_value.to_string()
        except Exception:
            return
        if not raw:
            return
        flag, sep, path = raw.partition("\t")
        if flag == "tip":      # lazy hover-tooltip request for node `path`
            if path:
                self._send_tip(path)
            return
        if not sep:            # no flag prefix → treat the whole value as a path
            flag, path = "0", flag
        if path:
            self.emit("node-activated-new-tab" if flag == "1" else "node-activated",
                      path)

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
        except Exception:
            pass
        try:
            self._web.try_close()
        except Exception:
            pass


def _transparent():
    from gi.repository import Gdk
    rgba = Gdk.RGBA()
    rgba.parse("rgba(0,0,0,0)")
    return rgba
