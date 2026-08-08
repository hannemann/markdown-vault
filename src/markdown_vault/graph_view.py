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

from gi.repository import Gtk, WebKit, GObject, GLib


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:transparent;
    font:12px system-ui,sans-serif;color:#888}
  #svg{width:100%;height:100%;display:block;cursor:grab}
  #svg.grabbing{cursor:grabbing}
  .edge{stroke:#8888;stroke-width:1.3;fill:none}
  .edge.out{stroke:#5b9bd5cc;stroke-width:1.6;marker-end:url(#arrow)}
  .node circle{stroke:rgba(0,0,0,.35);stroke-width:1;cursor:pointer}
  .node.center circle{stroke:#e66100;stroke-width:3}
  .node text{fill:currentColor;pointer-events:none;paint-order:stroke;
    stroke:rgba(127,127,127,.35);stroke-width:3px}
  .faded{opacity:.12;transition:opacity .1s}
  #legend{position:absolute;left:8px;bottom:6px;font-size:11px;opacity:.85}
  #legend span{margin-right:10px;white-space:nowrap}
  #legend i{display:inline-block;width:9px;height:9px;border-radius:50%;
    margin-right:4px;vertical-align:-1px}
  #empty{position:absolute;inset:0;display:none;align-items:center;
    justify-content:center;color:#999;font-style:italic}
</style></head>
<body>
<svg id="svg"><defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
    markerHeight="6" orient="auto-start-reverse">
    <path d="M0 0L10 5L0 10z" fill="#5b9bd5cc"/></marker>
</defs><g id="view"><g id="edges"></g><g id="nodes"></g></g></svg>
<div id="legend"></div>
<div id="empty">no connections</div>
<script>
const SVGNS="http://www.w3.org/2000/svg";
const svg=document.getElementById("svg"), view=document.getElementById("view");
const gEdges=document.getElementById("edges"), gNodes=document.getElementById("nodes");
const legend=document.getElementById("legend"), empty=document.getElementById("empty");
let W=innerWidth,H=innerHeight,cx=W/2,cy=H/2;
let nodes=[],links=[],byId={},centerId=null,adj={};
let tx=0,ty=0,scale=1,alpha=0,raf=0;

function radius(n){return 5+Math.min(9,Math.sqrt(n.degree||0)*2)+(n.center?3:0);}

function setGraph(payload){
  const prev={}; nodes.forEach(n=>prev[n.id]=n);
  const arr=payload.nodes||[];
  nodes=arr.map((n,i)=>{
    const p=prev[n.id]||{}, a=2*Math.PI*i/Math.max(1,arr.length);
    return Object.assign({},n,{
      x:p.x!==undefined?p.x:cx+Math.cos(a)*80,
      y:p.y!==undefined?p.y:cy+Math.sin(a)*80, vx:0,vy:0});
  });
  byId={}; nodes.forEach(n=>byId[n.id]=n);
  centerId=null; nodes.forEach(n=>{if(n.center)centerId=n.id;});
  links=(payload.edges||[]).filter(e=>byId[e.source]&&byId[e.target]);
  adj={}; nodes.forEach(n=>adj[n.id]=new Set());
  links.forEach(e=>{adj[e.source].add(e.target);adj[e.target].add(e.source);});
  buildLegend(nodes);
  empty.style.display=(nodes.length<=1&&links.length===0)?"flex":"none";
  render(); alpha=1; kick();
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
    const t=document.createElementNS(SVGNS,"title"); t.textContent=label(n);
    c.appendChild(t);
    const tx=document.createElementNS(SVGNS,"text");
    tx.setAttribute("x",radius(n)+3); tx.setAttribute("y",4);
    tx.textContent=n.center?label(n):"";
    g.appendChild(c); g.appendChild(tx); gNodes.appendChild(g);
    elNodes[n.id]={g,c,tx};
    c.addEventListener("pointerenter",()=>highlight(n.id));
    c.addEventListener("pointerleave",()=>highlight(null));
    c.addEventListener("click",ev=>{ev.stopPropagation();
      if(window.webkit&&webkit.messageHandlers&&webkit.messageHandlers.graph)
        webkit.messageHandlers.graph.postMessage(n.id);});
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
function highlight(id){
  const near=id?new Set([id,...adj[id]]):null;
  nodes.forEach(n=>elNodes[n.id]&&elNodes[n.id].g.classList.toggle("faded",
    near?!near.has(n.id):false));
  elEdges.forEach(({l,e})=>l.classList.toggle("faded",
    near?!(near.has(e.source)&&near.has(e.target)):false));
}

function step(){
  const K=6000, L=70;
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i],b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy||0.01;
    let f=K/d2, d=Math.sqrt(d2); dx/=d;dy/=d;
    a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;
  }
  links.forEach(e=>{const a=byId[e.source],b=byId[e.target];
    let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||0.01,f=(d-L)*0.02;
    dx/=d;dy/=d; a.vx+=dx*f;a.vy+=dy*f; b.vx-=dx*f;b.vy-=dy*f;});
  nodes.forEach(n=>{
    if(n.id===centerId){n.x=cx;n.y=cy;n.vx=0;n.vy=0;return;}
    n.vx+=(cx-n.x)*0.006; n.vy+=(cy-n.y)*0.006;   // gentle centering
    if(n.fixed)return;
    n.vx*=0.85; n.vy*=0.85; n.x+=n.vx*alpha; n.y+=n.vy*alpha;
  });
  alpha*=0.98;
}
function kick(){ if(raf)return; const loop=()=>{ step(); positions();
  if(alpha>0.02){raf=requestAnimationFrame(loop);} else {raf=0;} }; raf=requestAnimationFrame(loop);}

function makeDraggable(g,n){
  let drag=false;
  g.addEventListener("pointerdown",ev=>{drag=true;n.fixed=true;
    g.setPointerCapture(ev.pointerId);ev.stopPropagation();});
  g.addEventListener("pointermove",ev=>{if(!drag)return;
    n.x+=ev.movementX/scale;n.y+=ev.movementY/scale;positions();});
  g.addEventListener("pointerup",ev=>{drag=false;n.fixed=false;
    alpha=Math.max(alpha,0.4);kick();});
}
// pan + zoom on the background
let pan=false,px=0,py=0;
svg.addEventListener("pointerdown",ev=>{pan=true;px=ev.clientX;py=ev.clientY;
  svg.classList.add("grabbing");});
svg.addEventListener("pointermove",ev=>{if(!pan)return;
  tx+=ev.clientX-px;ty+=ev.clientY-py;px=ev.clientX;py=ev.clientY;positions();});
window.addEventListener("pointerup",()=>{pan=false;svg.classList.remove("grabbing");});
svg.addEventListener("wheel",ev=>{ev.preventDefault();
  const f=ev.deltaY<0?1.1:0.9,mx=ev.clientX,my=ev.clientY;
  tx=mx-(mx-tx)*f;ty=my-(my-ty)*f;scale*=f;positions();},{passive:false});
addEventListener("resize",()=>{W=innerWidth;H=innerHeight;cx=W/2;cy=H/2;
  alpha=Math.max(alpha,0.3);kick();});
window.setGraph=setGraph;
</script></body></html>
"""


class GraphView(Gtk.Box):
    __gsignals__ = {
        # node-activated(file_path): the user clicked a node.
        "node-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
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

    def set_graph(self, payload: dict) -> None:
        """Render *payload* ({"nodes": [...], "edges": [...]}); safe before load."""
        self._pending = payload
        if self._ready:
            self._push(payload)

    def _push(self, payload: dict) -> None:
        js = "window.setGraph(%s);" % json.dumps(payload)
        self._web.evaluate_javascript(js, -1, None, None, None, None, None)

    def _on_load_changed(self, _web, event) -> None:
        if event == WebKit.LoadEvent.FINISHED:
            self._ready = True
            if self._pending is not None:
                self._push(self._pending)

    def _on_message(self, _ucm, js_value) -> None:
        try:
            path = js_value.to_string()
        except Exception:
            return
        if path:
            self.emit("node-activated", path)

    def teardown(self) -> None:
        """Release the WebView (frees the WebKit web process)."""
        try:
            self._web.try_close()
        except Exception:
            pass


def _transparent():
    from gi.repository import Gdk
    rgba = Gdk.RGBA()
    rgba.parse("rgba(0,0,0,0)")
    return rgba
