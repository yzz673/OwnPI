/** Rebuild the bilingual seven-stage figures without a browser.
 * Requires @resvg/resvg-js (npm) and img2webp (libwebp tools) on PATH.
 * Run: node docs/assets/readme/render-workflow.cjs
 * The SVG files are editable final-state sources; WebP adds review-loop playback.
 */
const { Resvg } = require('@resvg/resvg-js');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const out = __dirname;
const fontFiles = ['/System/Library/Fonts/Supplemental/Arial.ttf','/System/Library/Fonts/Supplemental/Arial Bold.ttf','/System/Library/Fonts/Hiragino Sans GB.ttc'].filter(p => fs.existsSync(p));
const fontOptions = { loadSystemFonts:!fontFiles.length, fontFiles };
// Other systems need equivalent Arial / Noto Sans CJK fonts installed.
const C = { text:'#62766a', dim:'#708177', faint:'#77877e', border:'#8da69b', green:'#299b73', gold:'#b1842f', blue:'#5787b3' };
const labels = {
  zh: { stages:[['文献与背景'],['研究问题界定'],['方法设计'],['实验设计'],['实现与执行'],['分析与评审'],['产物打包与论文撰写']], prepare:'准备', output:'成果输出', review:'研究复盘', copy:['对先前的阶段进行复盘；需要','修正时返回对应阶段'], artifact:['整理论文、代码、图表与','可复现研究产物。'], revise:'返回修订', pass:'评审通过' },
  en: { stages:[['Literature','Grounding'],['Research','Framing'],['Method Design'],['Experiment','Design'],['Implementation','& Execution'],['Analysis & Review'],['Artifact Packaging','& Paper Writing']], prepare:'PREPARE', output:'RESEARCH OUTPUT', review:'Research review', copy:['Review prior stages; return to','the relevant stage when revision','is needed.'], artifact:['Organize the paper, code, figures,','and reproducible research outputs.'], revise:'REVISE', pass:'PASS' }
};
const cards = [[20,443,210,96],[338,443,152,96],[430,158,152,96],[730,158,152,96],[816,443,164,96],[569,620,174,96],[1082,399,230,166]];
const edges = [
  { to:2, p:[[230,491],[266,491],[302,491],[338,491]] },
  { to:3, p:[[402,443],[389,365],[419,309],[461,254]] },
  { to:4, p:[[582,160],[625,145],[686,145],[730,160]] },
  { to:5, p:[[850,254],[893,314],[923,382],[909,443]] },
  { to:6, p:[[880,539],[840,604],[788,645],[743,657]] },
  { to:'review', p:[[656,620],[656,570],[656,524],[656,475]], gold:true },
  { to:3, p:[[590,321],[565,301],[532,278],[506,254]], gold:true },
  { to:4, p:[[582,160],[625,145],[686,145],[730,160]] },
  { to:5, p:[[850,254],[893,314],[923,382],[909,443]] },
  { to:6, p:[[880,539],[840,604],[788,645],[743,657]] },
  { to:7, pass:true }
];
const route = [1,2,3,4,5,6,'review',3,4,5,6,7];
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const curve = p => `M ${p[0]} C ${p[1]} ${p[2]} ${p[3]}`;
function point(p,t) { return [0,1].map(k => (1-t)**3*p[0][k]+3*(1-t)**2*t*p[1][k]+3*(1-t)*t*t*p[2][k]+t**3*p[3][k]); }
function text(x,y,content,size=16,color=C.text,weight=600,anchor='start') { return `<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}">${esc(content)}</text>`; }
function svg(lang,phase=11,progress=1) {
  const l=labels[lang], active=route[phase], edge=phase ? edges[phase-1] : null;
  let s=`<svg xmlns="http://www.w3.org/2000/svg" width="2664" height="1490" viewBox="0 0 1332 745" role="img" aria-label="${lang==='zh'?'七阶段研究流程':'Seven-stage research workflow'}"><title>${lang==='zh'?'七阶段研究流程':'Seven-stage research workflow'}</title><defs><marker id="green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10Z" fill="${C.green}"/></marker><marker id="gold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10Z" fill="${C.gold}"/></marker></defs><g font-family="Arial, Hiragino Sans GB, Noto Sans CJK SC, sans-serif">`;
  const line=(d,color=C.green,opacity=1,dash=false) => `<path d="${d}" fill="none" stroke="${color}" stroke-width="${dash?1.8:2.2}" stroke-linecap="round" stroke-linejoin="round" opacity="${opacity}" ${dash?'stroke-dasharray="6 7"':''} marker-end="url(#${color===C.gold?'gold':'green'})"/>`;
  for (const e of edges.slice(0,5)) s+=line(curve(e.p),C.green,phase>=e.to-1?1:.3);
  // Return from analysis to review, then to one of the four upstream stages.
  s+=line(curve(edges[5].p),C.gold,active==='review'?1:.3,true);
  const spokes=[[[543,410],[525,432],[508,466],[490,491]],edges[6].p,[[722,321],[747,301],[780,278],[806,254]],[[769,410],[785,432],[801,466],[816,491]]];
  for(let i=0;i<4;i++) s+=line(curve(spokes[i]),C.gold,active==='review'&&i===1?1:.3,true);
  s+=line('M 743 668 H 952 M 1018 668 H 1197 V 565',C.green,phase===11?1:.3);
  s+=text(985,672,l.pass,11,C.green,600,'middle');
  s+=text(667,574,l.revise,11,C.gold);
  if(edge) {
    let p;
    if(edge.pass) { const total=454+103, d=progress*total; p=d<=454?[743+d,668]:[1197,668-(d-454)]; }
    else p=point(edge.p,progress);
    s+=`<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${edge.gold?C.gold:C.green}"/>`;
  }
  s+=text(20,426,l.prepare,13,C.faint);
  s+=text(1082,382,l.output,13,C.faint);
  cards.forEach(([x,y,w,h],i)=>{
    const n=i+1, color=n===6?C.gold:n===7?C.blue:C.green;
    const selected=active===n;
    s+=`<g data-stage="${n}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" fill="none" stroke="${selected?color:n===6?C.gold:n===7?C.blue:C.border}" stroke-width="${selected?2.5:1}"/>`;
    s+=text(x+14,y+(n===7?30:23),String(n).padStart(2,'0'),12.5,color,600);
    l.stages[i].forEach((t,j)=>s+=text(x+14,y+(n===7?55:47)+j*20,t,lang==='en'?15:16));
    if(n===7) l.artifact.forEach((t,j)=>s+=text(x+14,y+105+j*20,t,lang==='en'?12.5:14,C.dim,400));
    s+='</g>';
  });
  s+=`<g data-role="review"><rect x="543" y="321" width="226" height="154" rx="8" fill="none" stroke="${C.gold}" stroke-opacity="${active==='review'?1:.5}" stroke-width="${active==='review'?2:1}"/>`;
  s+=text(656,347,l.review,18,C.text,600,'middle');
  l.copy.forEach((t,j)=>s+=text(656,lang==='en'?368+j*16:379+j*19,t,lang==='en'?12:13.5,C.dim,400,'middle'));
  [2,3,4,5].forEach((n,i)=>{ const x=562+i*48; s+=`<g data-revisit-target="${n}"><rect x="${x}" y="419" width="44" height="44" rx="5" fill="none" stroke="${C.gold}" stroke-opacity="${n===3?1:.4}" stroke-width="${n===3?1.8:1}"/>`;s+=text(x+22,446,String(n).padStart(2,'0'),13,n===3?C.gold:C.dim,600,'middle')+'</g>'; });
  return s+'</g></g></svg>';
}
const metadata={description:'Seven-stage research workflow, synchronized with the active website runtimeFlow. Transparent bilingual animation with analysis-to-review and return-to-method iteration.',source:{website_commit:'0b0d69c0fc6d5a691c51cd7719514a5cd42069a8',element:'#runtimeFlow'},renderer:'@resvg/resvg-js; img2webp lossless',stage_count:7,revisit_targets:[2,3,4,5],files:{}};
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'ownpi-workflow-'));
try {
 for(const lang of ['zh','en']) {
  const source=svg(lang); fs.writeFileSync(path.join(out,`workflow-source.${lang}.svg`),source+'\n');
  const frames=[];
  for(let i=0;i<96;i++) {
   const frame=path.join(tmp,`${lang}-${String(i).padStart(3,'0')}.png`);
   fs.writeFileSync(frame,new Resvg(svg(lang,Math.floor(i/8),(i%8)/7),{font:fontOptions}).render().asPng());
   frames.push(frame);
  }
  const name=`hd-workflow.${lang}.webp`, target=path.join(out,name);
  const result=spawnSync('img2webp',['-loop','0','-lossless','-m','4','-d','125',...frames,'-o',target],{encoding:'utf8',maxBuffer:1024*1024});
  if(result.status!==0) throw new Error(result.stderr||result.error?.message||'img2webp failed');
  const bytes=fs.readFileSync(target);
  metadata.files[lang]={file:name,source:`workflow-source.${lang}.svg`,sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length,width:2664,height:1490,duration_ms:12000,stages:labels[lang].stages.map(v=>v.join(' '))};
 }
 fs.writeFileSync(path.join(out,'workflow-figure.json'),JSON.stringify(metadata,null,2)+'\n');
} finally { fs.rmSync(tmp,{recursive:true,force:true}); }
