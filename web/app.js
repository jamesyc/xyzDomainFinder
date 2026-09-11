const $ = (id) => document.getElementById(id);
const families = [
  ['uniform','Uniform digits','●'],['repeat','Repeated blocks','↻'],['palindrome','Palindromes','◇'],
  ['sequence','Sequences','↗'],['pair','Paired digits','∷'],['chunks','Digit chunks','▥'],
  ['near_repeat','Near repetition','≈'],['counting_blocks','Counting blocks','↗'],
  ['stepping_pairs','Stepping pairs','∷'],['consecutive_run','Consecutive runs','↗'],
  ['round','Zero endings','○'],['date','Dates','□'],['constant','Constants','π'],
  ['digit_diversity','Few distinct digits','⋮'],
];
const labels=Object.fromEntries(families.map(([key,label])=>[key,label]));
const tagLabels={uniform:'Uniform',repeat:'Repeat',palindrome:'Palindrome',sequence:'Sequence',pair:'Paired',chunks:'Chunks',near_repeat:'Near repeat',counting_blocks:'Counting',stepping_pairs:'Stepping pairs',consecutive_run:'Sequence run',round:'Zero ending',date:'Date',constant:'Constant',digit_diversity:'Few digits'};
const states={unchecked:'Unchecked',available:'Available',unavailable:'Unavailable',unknown:'Unknown'};
const pageSize=20;
let data=[],total=0,metadata={},pattern='',length='',page=1,current=null,toastTimer,searchTimer,requestId=0,controller,detailId=0;
const escape=(value)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=(value)=>Number(value).toLocaleString();
const reasons=(row)=>row.reasons?row.reasons.split(';'):[];
const tags=(row)=>reasons(row).map(reason=>`<span class="tag ${escape(reason)}">${escape(tagLabels[reason]||reason)}</span>`).join('');
const params=()=>new URLSearchParams({search:$('search').value,pattern,length,state:$('state').value,min_score:$('min-score').value||'0',page:String(page),page_size:String(pageSize)});

function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;},2400);}
async function copy(domain){try{await navigator.clipboard.writeText(domain);toast(`Copied ${domain}`);}catch{toast('Select the domain text to copy it manually.');}}

async function detail(row){
  const id=++detailId;current=row;
  $('detail-name').textContent=row.domain;
  $('detail-content').textContent='Loading score breakdown…';
  $('details').showModal();
  try{
    const response=await fetch('/api/domain?'+new URLSearchParams({domain:row.domain}),{cache:'no-store'});
    const result=await response.json();
    if(id!==detailId)return;
    if(!response.ok)throw new Error(result.error||'Candidate could not be read.');
    const price=value=>value==null?'Not quoted':`${value} ${result.currency||'(currency unknown)'}`;
    const totals={};
    result.properties.forEach(prop=>{totals[prop.family]=(totals[prop.family]||0)+prop.awarded;});
    const age=result.checked_at?(Date.now()-Date.parse(result.checked_at))/60000:null;
    const freshness=age===null?'Not checked':Number.isFinite(age)&&age>=0&&age<15?'Recent · under 15 minutes':'Recheck before relying on this result';
    const fields=[['Rank in length',`#${number(result.rank)} of ${number(metadata.cohorts?.[result.length]?.retained||0)} · ${result.length} digits`],['Availability',states[result.availability]||'Unknown'],['Last checked',result.checked_at?new Date(result.checked_at).toLocaleString():'Not checked'],['Observation age',freshness],['Registrar',result.provider||'Not checked'],['Price class',result.premium===1?'Premium':result.premium===0?'Standard':'Not reported'],['Registration',price(result.registration_price)],['Renewal',price(result.renewal_price)]];
    $('detail-content').innerHTML=`<div class="score-hero"><strong>${escape(result.score)}</strong><span>interest points</span></div><div class="family-awards">${Object.entries(totals).map(([family,points])=>`<span>${escape(family)} <b>+${escape(points)}</b></span>`).join('')}</div><h3 class="breakdown-title">Why this number stands out</h3><div class="breakdown">${result.properties.map(prop=>`<div class="property"><div><strong>${escape(prop.title)}</strong><p>${escape(prop.evidence)}</p>${prop.awarded===0?'<small>Covered by a stronger rule in this family</small>':''}</div><span class="property-points ${prop.awarded?'':'covered'}">${prop.awarded?`+${escape(prop.awarded)}`:'—'}</span></div>`).join('')}</div><dl class="detail-list">${fields.map(([label,value])=>`<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl>${result.check_error?`<p class="detail-note">Check could not be completed: ${escape(result.check_error)}.</p>`:''}${result.quote_note?`<p class="detail-note">${escape(result.quote_note)}</p>`:''}<p class="detail-note">Points describe interesting properties, not resale value or availability. Scoring profile: ${escape(metadata.ranking_version)}.</p>`;
  }catch(error){if(id===detailId)$('detail-content').textContent=error.message;}
}

function draw(){
  const pages=Math.max(1,Math.ceil(total/pageSize));
  const start=(page-1)*pageSize;
  $('result-count').textContent=`${number(total)} ${total===1?'name':'names'}`;
  $('active-pattern').textContent=labels[pattern]||'All properties';
  $('rows').innerHTML=data.map((row,index)=>`<tr>
    <td><span class="hash">#</span>${number(row.rank)}</td>
    <td><button class="domain-button" data-detail="${index}" aria-label="View ${escape(row.domain)}">${escape(row.domain.replace(/\.xyz$/,''))}<span class="tld">.xyz</span></button></td>
    <td><span class="score-badge">${escape(row.score)}</span></td>
    <td><div class="tags">${tags(row)}</div></td>
    <td class="length-cell">${escape(row.length)} digits</td>
    <td><span class="status ${escape(row.availability)}">${escape(states[row.availability]||'Unknown')}</span></td>
    <td><button class="copy-button" data-copy="${index}" aria-label="Copy ${escape(row.domain)}" title="Copy domain">⧉</button></td></tr>`).join('');
  $('rows').querySelectorAll('[data-detail]').forEach(button=>button.addEventListener('click',()=>detail(data[Number(button.dataset.detail)])));
  $('rows').querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',()=>copy(data[Number(button.dataset.copy)].domain)));
  $('empty').hidden=total!==0;
  $('range').textContent=total?`${number(start+1)}–${number(Math.min(start+pageSize,total))} of ${number(total)} names`:'0 names';
  $('page').textContent=`${page} / ${pages}`;
  $('previous').disabled=page<=1;$('next').disabled=page>=pages;$('export').disabled=!total;
  $('patterns').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.pattern===pattern)));
  $('cohorts').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.length===length)));
}

function drawOverview(){
  $('total').textContent=number(metadata.row_count);$('sidebar-total').textContent=number(metadata.row_count);
  $('examined').textContent=number(metadata.examined);$('checked').textContent=number(metadata.checked);
  $('checked-caption').textContent=metadata.checked?`${number(metadata.row_count-metadata.checked)} names unchecked`:'No registration checks yet';
  $('cap-warning').hidden=metadata.cap_reached!=='true';
  $('built-at').textContent=`${metadata.ranking_version} · Built ${new Date(metadata.created_at).toLocaleDateString()}`;
  $('patterns').innerHTML=`<button class="pattern-button" data-pattern="" aria-pressed="true"><span class="pattern-symbol">▦</span>All properties<span class="pattern-count">${number(metadata.row_count)}</span></button>`+families.map(([key,label,symbol])=>`<button class="pattern-button" data-pattern="${key}" aria-pressed="false"><span class="pattern-symbol" aria-hidden="true">${symbol}</span>${label}<span class="pattern-count">${number(metadata.pattern_counts[key]||0)}</span></button>`).join('');
  $('patterns').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{pattern=button.dataset.pattern;page=1;load();}));
  $('cohorts').innerHTML=[['','All lengths',metadata.row_count],...[6,7,8,9].map(n=>[String(n),`${n} digits`,metadata.cohorts[n]?.retained||0])].map(([key,label,count])=>`<button type="button" class="cohort-button" data-length="${key}" aria-pressed="${key===length}">${label}<span>${number(count)}</span></button>`).join('');
  $('cohorts').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{length=button.dataset.length;page=1;load();}));
  $('cohort-info').textContent=length?`${length}-digit collection · retained cutoff ${metadata.cohorts[length]?.cutoff??'—'} points`:'Each length has its own retention budget and rank.';
}

async function load(){
  const id=++requestId;
  controller?.abort();controller=new AbortController();
  $('refresh').disabled=true;$('export').disabled=true;$('error').hidden=true;
  $('rows').setAttribute('aria-busy','true');
  try{
    const response=await fetch('/api/catalog?'+params(),{cache:'no-store',signal:controller.signal});
    const payload=await response.json();
    if(id!==requestId)return;
    if(!response.ok)throw new Error(payload.error||'The catalog could not be loaded.');
    data=payload.rows;total=payload.total;metadata=payload.metadata;
    const last=Math.max(1,Math.ceil(total/pageSize));
    if(page>last){page=last;return load();}
    drawOverview();draw();
  }catch(error){
    if(error.name==='AbortError'||id!==requestId)return;
    data=[];total=0;draw();
    $('error').textContent=error.message;$('error').hidden=false;
    ['total','sidebar-total','examined','checked'].forEach(id=>{$(id).textContent='—';});
  }finally{if(id===requestId){$('refresh').disabled=false;$('rows').setAttribute('aria-busy','false');}}
}

function reset(){clearTimeout(searchTimer);pattern='';length='';page=1;$('filters').reset();load();}
$('filters').addEventListener('submit',event=>event.preventDefault());
['search','min-score'].forEach(id=>$(id).addEventListener('input',()=>{page=1;clearTimeout(searchTimer);controller?.abort();$('export').disabled=true;searchTimer=setTimeout(load,180);}));
$('state').addEventListener('change',()=>{page=1;load();});
$('previous').addEventListener('click',()=>{page--;load();});
$('next').addEventListener('click',()=>{page++;load();});
$('refresh').addEventListener('click',load);
$('reset').addEventListener('click',reset);$('all-catalog').addEventListener('click',reset);
$('close-details').addEventListener('click',()=>{detailId++;$('details').close();});
$('copy-detail').addEventListener('click',()=>{if(current)copy(current.domain);});
$('export').addEventListener('click',()=>{const anchor=document.createElement('a');anchor.href='/export.csv?'+params();anchor.download='xyz-shortlist.csv';anchor.click();toast('CSV download requested');});
load();
