/*! audience anonyme */
((n,l,d)=>{let R=/(^|\/)(commander|alertes\/)/,p=l.pathname,q=new URLSearchParams(l.search),g=p.split('/')[1],c=R.test(p),A=(k,f)=>d.addEventListener(k,f,!0),b,t;
if(n.doNotTrack=='1'||n.globalPrivacyControl||l.hostname!='outils.vread.fr')return;
b={site:/^(kit-|veille-|declaration-)/.test(g)?g:'hub',page:p,ref_domain:d.referrer.split('/')[2],device:innerWidth<768?'mobile':'desktop'};
for(g of['source','medium','campaign'])b['utm_'+g]=q.get('utm_'+g);
t=v=>{b.event=v;b.ts=Date.now();try{fetch('https://script.google.com/macros/s/AKfycbzD1IRL8QALluKGZxgGiORFP6-DkC_qFJYR9aOxZiimxa06nbCqpzUI-9iLblp6V2VY/exec?action=track',{method:'POST',keepalive:!0,credentials:'omit',redirect:'manual',body:JSON.stringify(b)}).catch(e=>0)}catch{}};
t('view');c&&t('checkout_view');
A('submit',e=>e.target.id=='octave-pay'&&t('checkout_start'));
A('click',e=>{let a=e.target.closest?.('a,button'),h=a?.dataset.mesure;h?t(h):a&&!c&&R.test(a.getAttribute('href'))&&t('cta_click')})})(navigator,location,document)
