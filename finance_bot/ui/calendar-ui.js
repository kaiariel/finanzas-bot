/* Agenda familiar: la misma fuente que /hoy; independiente de filtros financieros. */
window.createFinanceCalendar = function ({getData, refreshData, mutate, notify, openDialog, closeDialog}) {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {breakfast:'Desayuno', meal:'Comida', reminder:'Recordatorio', event:'Evento', note:'Nota'};
  const emojis = {breakfast:'🥐', meal:'🍽️', reminder:'🔔', event:'🎈', note:'📝'};
  function eventEmoji(e) {
    if(e.kind!=='breakfast')return emojis[e.kind]||'📅';
    if(/lácteos/i.test(e.title))return '🥛';
    if(/fruta/i.test(e.title))return '🍐';
    if(/bocadillo/i.test(e.title))return '🥪';
    if(/galletas|bizcochos/i.test(e.title))return '🍪';
    return emojis.breakfast;
  }
  const repetitions = {once:'Sin repetición', daily:'Cada día', weekly:'Cada semana', monthly:'Cada mes', yearly:'Cada año'};
  const dishText = e => e.kind==='meal' && e.details ? e.details.replace(/^(Primer plato|Segundo plato|Pan y postre):\s*/gmi,'') : e.title;
  const formatDate = value => new Intl.DateTimeFormat('es-ES', {weekday:'long', day:'numeric', month:'long', year:'numeric', timeZone:'UTC'}).format(new Date(value+'T12:00:00Z'));
  const monthName = value => new Intl.DateTimeFormat('es-ES', {month:'long', year:'numeric', timeZone:'UTC'}).format(new Date(value+'-01T12:00:00Z'));
  let month=getData().today.slice(0,7), selected=getData().today, view='calendar', includeSkipped=false, cache=new Map(), generation=0, saving=false, editOriginal=null, scopeDrafts={}, lastScope='series';
  function update() {
    generation++; cache.clear();
    const data=getData(), cal=data.calendar;
    if(cal) {
      let key=cal.start.slice(0,7);
      while(key<=cal.end.slice(0,7)) {
        cache.set(key, {...cal, occurrences:cal.occurrences.filter(e=>e.date.startsWith(key))});
        key=shiftMonth(key,1);
      }
    }
    $('addCalendarEvent').hidden=!data.editable; $('addSelectedDayEvent').hidden=!data.editable; $('addTodayEvent').hidden=!data.editable;
    renderToday();
  }
  function shiftMonth(key,delta){const d=new Date(key+'-01T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+delta);return d.toISOString().slice(0,7)}
  function card(e,compact=false) {
    const status=e.status==='completed'?'Completado':e.status==='skipped'?'Omitido':'Pendiente';
    const action=e.status==='pending'?'Completar':'Volver a pendiente';
    const source=e.sourceUrl?'<a class="calendar-source" href="'+esc(e.sourceUrl)+'" target="_blank" rel="noopener">Ver documento original ↗</a>':'';
    return '<article class="agenda-card '+esc(e.kind)+(e.status==='skipped'?' skipped':'')+'"><div class="agenda-card-heading"><span aria-hidden="true" class="module-icon '+(e.kind==='breakfast'?'peach':e.kind==='meal'?'mint':'lavender')+'">'+eventEmoji(e)+'</span><div><span class="agenda-kind">'+labels[e.kind]+(e.time?' · '+esc(e.time):'')+'</span><h3>'+esc(e.title)+'</h3></div>'+(e.status!=='pending'?'<span class="agenda-status">'+status+'</span>':'')+'</div>'+(e.person?'<p class="agenda-person">'+esc(e.person)+'</p>':'')+(e.details?'<p class="agenda-details">'+esc(e.details)+'</p>':'')+(e.recurrence && e.recurrence!=='once'?'<p class="agenda-recurrence">↻ '+esc(repetitions[e.recurrence])+'</p>':'')+source+(getData().editable?'<div class="agenda-actions"><button type="button" data-calendar-edit="'+e.id+'" data-date="'+e.date+'"><span aria-hidden="true">✏️ </span>Editar detalles</button><button type="button" class="secondary" data-calendar-status="'+(e.status==='pending'?'completed':'pending')+'" data-event-id="'+e.id+'" data-date="'+e.date+'">'+action+'</button>'+(e.status!=='skipped'?'<button type="button" class="quiet" data-calendar-status="skipped" data-event-id="'+e.id+'" data-date="'+e.date+'">Omitir este día</button>':'')+'</div>':'')+'</article>';
  }
  function renderToday() {
    const data=getData(), items=data.calendar?.today||[];
    $('todayDate').textContent=formatDate(data.today);
    $('todayContent').innerHTML=items.length?items.map(e=>card(e,true)).join(''):'<div class="agenda-empty"><strong>Tu día está despejado.</strong><p>No hay comidas ni recordatorios programados para hoy. Puedes añadir algo al calendario.</p></div>';
    $('addTodayEvent').hidden=!data.editable;
  }
  function render() {
    $('calendarMonth').value=month;
    $('calendarSelectedDate').textContent=formatDate(selected);
    $('calendarMonthPanel').hidden=view!=='calendar';
    document.querySelectorAll('[data-calendar-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.calendarView===view)));
    const loaded=cache.get(month);
    if(!loaded) {
      $('familyCalendar').innerHTML='<p class="empty">Cargando el calendario…</p>';
      $('calendarAgenda').innerHTML='';
      if(!getData().editable) { $('familyCalendar').innerHTML='<p class="empty">Este mes no está incluido en la copia. Abre el panel local para consultar otras fechas.</p>'; return; }
      const token=generation, requested=month;
      fetch('/api/calendar?month='+encodeURIComponent(requested),{cache:'no-store'}).then(async r=>{const value=await r.json();if(!r.ok)throw Error(value.error||'No se pudo consultar el calendario.');return value}).then(value=>{if(token!==generation)return;cache.set(requested,value);if(month===requested)render()}).catch(error=>{if(month===requested){$('familyCalendar').innerHTML='<p class="empty">'+esc(error.message)+'</p>';notify(error.message,true)}});
      return;
    }
    const items=loaded.occurrences.filter(e=>includeSkipped||e.status!=='skipped');
    $('calendarCount').textContent=items.length+' eventos del mes';
    if(view==='calendar') {
      const first=new Date(month+'-01T12:00:00Z'), offset=(first.getUTCDay()+6)%7, last=new Date(first.getUTCFullYear(),first.getUTCMonth()+1,0).getDate();
      let html='<div class="family-calendar-grid">'+['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'].map(d=>'<span class="calendar-weekday">'+d+'</span>').join('')+'<span class="calendar-gap"></span>'.repeat(offset);
      for(let day=1;day<=last;day++) {
        const key=month+'-'+String(day).padStart(2,'0'), dayItems=items.filter(e=>e.date===key), title=formatDate(key)+': '+dayItems.map(e=>dishText(e)).join(', ');
        html+='<button type="button" class="family-calendar-day '+(selected===key?'selected ':'')+(getData().today===key?'is-today':'')+'" data-calendar-day="'+key+'" aria-label="'+esc(title)+'" aria-pressed="'+(selected===key)+'"><b>'+day+'</b><span class="calendar-day-labels">'+dayItems.slice(0,3).map(e=>'<span class="calendar-chip '+esc(e.kind)+'"><strong><span aria-hidden="true">'+eventEmoji(e)+' </span>'+esc(labels[e.kind])+'</strong><span>'+esc(dishText(e))+'</span></span>').join('')+(dayItems.length>3?'<small>+'+(dayItems.length-3)+' más</small>':'')+'</span><span class="calendar-dot-count">'+(dayItems.length?dayItems.length:'')+'</span></button>';
      }
      $('familyCalendar').innerHTML=html+'</div>';
      const dayItems=items.filter(e=>e.date===selected);
      $('calendarDayHeading').hidden=false;
      $('calendarAgenda').innerHTML=dayItems.length?dayItems.map(e=>card(e)).join(''):'<p class="empty">No hay eventos para este día. Usa «Añadir a este día» para crear uno.</p>';
    } else {
      $('calendarDayHeading').hidden=true;
      $('familyCalendar').innerHTML='';
      const days=[...new Set(items.map(e=>e.date))];
      $('calendarAgenda').innerHTML=days.length?days.map(day=>'<section class="agenda-day"><h3>'+esc(formatDate(day))+'</h3><div class="today-grid">'+items.filter(e=>e.date===day).map(e=>card(e)).join('')+'</div></section>').join(''):'<p class="empty">No hay eventos en este mes.</p>';
    }
    const name=monthName(month); $('calendarMonthName').textContent=name.charAt(0).toUpperCase()+name.slice(1);
  }
  function rules() {
    const f=$('calendarEventForm'), occurrence=!!f.elements.id.value&&f.elements.scope.value==='occurrence';
    f.querySelectorAll('[data-calendar-schedule]').forEach(label=>{label.hidden=false;label.querySelector('input,select').disabled=occurrence});
    $('calendarChangeRecurrence').hidden=!occurrence;
    $('calendarScopeHelp').hidden=!occurrence;
    const weekly=!occurrence&&f.elements.recurrence.value==='weekly'; $('calendarWeekdays').hidden=!weekly;
    f.querySelectorAll('[name=weekdays]').forEach(input=>input.disabled=!weekly);
    if(!occurrence){f.elements.endDate.disabled=f.elements.recurrence.value==='once'}
    const rule=f.elements.recurrence.value;
    const monthlyDay=Number(f.elements.startDate.value.slice(-2));
    $('calendarRuleNote').textContent=occurrence?'Estás editando solo el '+formatDate(f.elements.occurrenceDate.value)+'. Para cambiar la repetición, elige «Toda la serie».':rule==='weekly'?'Se repetirá cada semana en los días que marques. Aparecerá en Hoy y /hoy.':rule==='monthly'?'Se repetirá el día '+monthlyDay+' de cada mes. Los meses que no tengan ese día se omiten.':rule==='once'?'Aparecerá una sola vez en la fecha elegida, en Hoy y /hoy.':'Aparece en Hoy y /hoy en sus fechas. Los días inexistentes se omiten.';
  }
  function openEvent(id=null,day=selected) {
    if(!getData().editable)return;
    const f=$('calendarEventForm');f.reset();editOriginal=(getData().calendar?.events||[]).find(e=>e.id===Number(id))||null;
    const occurrence=[...(getData().calendar?.today||[]),...(cache.get(day.slice(0,7))?.occurrences||[])].find(e=>e.id===Number(id)&&e.date===day);
    const value=occurrence||editOriginal||{title:'',details:'',kind:'reminder',person:'',startDate:day,endDate:'',time:'',recurrence:'once',weekdays:[]};
    ['title','details','kind','person','startDate','endDate','time','recurrence'].forEach(key=>{f.elements[key].value=value[key]||''});
    f.elements.id.value=id||''; f.elements.occurrenceDate.value=day;
    const recurring=!!id && value.recurrence!=='once';
    f.elements.scope.value=recurring?'occurrence':'series';
    lastScope=f.elements.scope.value;scopeDrafts={series:editOriginal,occurrence:value};
    f.querySelectorAll('[name=weekdays]').forEach(input=>input.checked=value.weekdays.includes(Number(input.value)));
    $('calendarScopeLabel').hidden=!recurring; $('archiveCalendarEvent').hidden=!id;
    $('calendarEventTitle').textContent=id?'Editar evento':'Añadir al calendario';$('calendarEventError').hidden=true;
    rules();openDialog('calendarEventModal');
  }
  $('calendarEventForm').elements.scope.addEventListener('change',()=>{
    const f=$('calendarEventForm');
    const fields=['title','details','kind','person','startDate','endDate','time','recurrence'];
    scopeDrafts[lastScope]=Object.fromEntries(fields.map(k=>[k,f.elements[k].value]));
    scopeDrafts[lastScope].weekdays=[...f.querySelectorAll('[name=weekdays]:checked')].map(i=>Number(i.value));
    const draft=scopeDrafts[f.elements.scope.value];
    if(draft){
      fields.forEach(k=>f.elements[k].value=draft[k]||'');
      f.querySelectorAll('[name=weekdays]').forEach(i=>i.checked=draft.weekdays.includes(Number(i.value)));
    }
    lastScope=f.elements.scope.value;
    rules();
  });
  $('calendarChangeRecurrence').addEventListener('click',()=>{
    const scope=$('calendarEventForm').elements.scope; scope.value='series';scope.dispatchEvent(new Event('change'));
    $('calendarEventForm').elements.recurrence.focus();
  });
  $('calendarEventForm').elements.recurrence.addEventListener('change',()=>{
    const f=$('calendarEventForm');
    if(f.elements.recurrence.value==='weekly'&&!f.querySelector('[name=weekdays]:checked')){
      const weekday=(new Date(f.elements.startDate.value+'T12:00:00Z').getUTCDay()+6)%7;
      const input=f.querySelector('[name=weekdays][value="'+weekday+'"]');if(input)input.checked=true;
    }
    rules();
  });
  $('calendarEventForm').elements.startDate.addEventListener('change',rules);
  $('calendarEventForm').addEventListener('submit',async event=>{
    event.preventDefault();if(saving)return;
    const f=event.target, id=f.elements.id.value, occurrence=id&&f.elements.scope.value==='occurrence';
    const payload={};['title','details','kind','person','time'].forEach(k=>payload[k]=f.elements[k].value);
    if(!occurrence){['startDate','endDate','recurrence'].forEach(k=>payload[k]=f.elements[k].value);payload.weekdays=[...f.querySelectorAll('[name=weekdays]:checked')].map(i=>Number(i.value))}
    saving=true;$('calendarEventError').hidden=true;const submit=f.querySelector('[type=submit]');submit.disabled=true;
    let saved=false;
    try {await mutate('/api/calendar/events'+(id?'/'+id:'')+(occurrence?'/'+f.elements.occurrenceDate.value:''),occurrence?{overrides:payload}:payload);saved=true;closeDialog('calendarEventModal',true);await refreshData();notify('Evento guardado en el calendario.');}
    catch(error){if(saved)notify('Evento guardado. Actualiza los datos para verlo.',true);else{$('calendarEventError').textContent=error.message;$('calendarEventError').hidden=false}}
    finally{saving=false;submit.disabled=false}
  });
  $('archiveCalendarEvent').addEventListener('click',async()=>{
    if(saving||!confirm('¿Retirar este evento y todas sus repeticiones del calendario?'))return;
    saving=true;
    try {await mutate('/api/calendar/events/'+$('calendarEventForm').elements.id.value,{},'DELETE');closeDialog('calendarEventModal',true);await refreshData();notify('Serie retirada del calendario.');}catch(e){notify(e.message,true)}finally{saving=false}
  });
  $('calendarMonth').addEventListener('change',()=>{if(!$('calendarMonth').value)return;month=$('calendarMonth').value;selected=month+'-01';render()});
  $('calendarShowSkipped').addEventListener('change',()=>{includeSkipped=$('calendarShowSkipped').checked;render()});
  $('addCalendarEvent').addEventListener('click',()=>openEvent());$('addTodayEvent').addEventListener('click',()=>openEvent(null,getData().today));
  $('addSelectedDayEvent').addEventListener('click',()=>openEvent());
  $('familyCalendar').addEventListener('keydown',event=>{
    const day=event.target.closest('[data-calendar-day]');
    const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];
    if(!day||delta===undefined||event.ctrlKey||event.altKey||event.metaKey)return;
    event.preventDefault();
    const date=new Date(day.dataset.calendarDay+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+delta);
    const next=date.toISOString().slice(0,10);
    if(!next.startsWith(month))return;
    selected=next;render();$('familyCalendar').querySelector('[data-calendar-day="'+next+'"]').focus();
  });
  document.addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.dataset.calendarDay){selected=b.dataset.calendarDay;render();$('calendarSelectedDate').focus({preventScroll:true});$('calendarDayHeading').scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})}
    if(b.dataset.calendarView){view=b.dataset.calendarView;render()}
    if(b.dataset.calendarMonthStep){month=shiftMonth(month,Number(b.dataset.calendarMonthStep));selected=month+'-01';render()}
    if(b.id==='calendarGoToday'){selected=getData().today;month=selected.slice(0,7);render()}
    if(b.dataset.calendarEdit){openEvent(b.dataset.calendarEdit,b.dataset.date)}
    if(b.dataset.calendarStatus){
      if(!getData().editable||saving)return;
      if(b.dataset.calendarStatus==='skipped'&&!confirm('¿Omitir el evento solo el '+formatDate(b.dataset.date)+'?'))return;
      saving=true;b.disabled=true;
      try {await mutate('/api/calendar/events/'+b.dataset.eventId+'/'+b.dataset.date,{status:b.dataset.calendarStatus});await refreshData();notify('Estado actualizado solo para este día.');}catch(e){notify(e.message,true)}finally{saving=false;b.disabled=false}
    }
  });
  return {update,render,renderToday};
};
