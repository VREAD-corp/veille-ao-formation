/*! audience anonyme */
((n,l,d)=>{let R=/(^|\/)(commander|alertes\/)/,p=l.pathname,q=new URLSearchParams(l.search),g=p.split('/')[1],c=R.test(p),A=(k,f)=>d.addEventListener(k,f,!0),K='octave_notrack',b,t,x,s,o;
if(n.doNotTrack=='1'||n.globalPrivacyControl||l.hostname!='outils.vread.fr')return;
try{s=localStorage;q.has(K)&&(q.get(K)=='1'?s.setItem(K,1):s.removeItem(K));x=s.getItem(K)=='1'&&'notrack'}catch{}
x=x||q.get(K)=='1'&&'notrack'||n.webdriver&&'webdriver'||/bot\b|crawl|spider|slurp|headless|lighthouse|pagespeed|inspectiontool|googleother|mediapartners|bingpreview|phantom|puppeteer|playwright|selenium/i.test(n.userAgent)&&'bot';
b={v:2,site:/^(kit-|veille-|declaration-|comprendre-|tcf-|visibilite-)/.test(g)?g:'hub',page:p,ref_domain:d.referrer.split('/')[2],device:innerWidth<768?'mobile':'desktop'};
x&&(b.excluded=x);
for(g of['source','medium','campaign'])b['utm_'+g]=q.get('utm_'+g);
t=v=>{b.event=v;b.ts=Date.now();try{fetch('https://script.google.com/macros/s/AKfycbzD1IRL8QALluKGZxgGiORFP6-DkC_qFJYR9aOxZiimxa06nbCqpzUI-9iLblp6V2VY/exec?action=track',{method:'POST',keepalive:!0,credentials:'omit',redirect:'manual',body:JSON.stringify(b)}).catch(e=>0)}catch{}};
o=()=>{t('view');c&&t('checkout_view')};
d.prerendering?A('prerenderingchange',o):d.visibilityState=='prerender'?A('visibilitychange',function v(){d.visibilityState!='prerender'&&(d.removeEventListener('visibilitychange',v,!0),o())}):o();
A('submit',e=>e.target.id=='octave-pay'&&t('checkout_start'));
A('click',e=>{let a=e.target.closest?.('a,button'),h=a?.dataset.mesure;h?t(h):a&&!c&&R.test(a.getAttribute('href'))&&t('cta_click')})})(navigator,location,document)
