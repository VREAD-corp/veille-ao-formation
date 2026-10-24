/**
 * Quote / bank-transfer form of the commander pages (#octave-devis inside #octave-devis-box), shipped by the
 * site builds as checkout/devis.js next to pay.js. Shown ONLY when the build config says quotesEnabled: true
 * (sales.config.json → window.OCTAVE_PAY.quotesEnabled); the server has its own flag (QUOTES_ENABLED).
 * Posts { action: 'quote', ... } to the same Apps Script endpoint as pay.js (text/plain: no CORS preflight).
 * The server answers { ok, quote: 'D-2026-0001', email_masked } and does everything else (Stripe Customer +
 * bank-transfer PaymentIntent, PDF, e-mail, Drive, alert); this page never sees the IBAN.
 */
(function () {
  'use strict';
  var cfg = window.OCTAVE_PAY || {};
  var box = document.getElementById('octave-devis-box');
  var form = document.getElementById('octave-devis');
  if (!form || !cfg.endpoint || cfg.quotesEnabled !== true) {
    if (box) box.hidden = true;
    return;
  }
  if (box) box.hidden = false;
  var btn = document.getElementById('devis-button');
  var errBox = document.getElementById('devis-error');
  var okBox = document.getElementById('devis-ok');
  var link = form.querySelector('.terms-link');
  if (link && cfg.termsUrl) link.href = cfg.termsUrl;
  // One attempt id per page load: a double click or a retry reuses the same quote server-side.
  var attempt = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(16)), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');

  function product() {
    var r = form.querySelector('input[name="product"]:checked');
    return r ? r.value : cfg.product;
  }
  // Same rules as extraFields() in pay.js (name = key; data-octave-extra="bool" for a yes/no checkbox).
  function extra() {
    var out = {};
    Array.prototype.forEach.call(form.querySelectorAll('[data-octave-extra]'), function (el) {
      var name = el.name;
      if (!name) return;
      if (el.type === 'checkbox' && el.getAttribute('data-octave-extra') === 'bool') { out[name] = el.checked === true; return; }
      if (el.type === 'checkbox') { out[name] = out[name] || []; if (el.checked) out[name].push(el.value); return; }
      if (el.type === 'radio') { if (el.checked) out[name] = el.value; return; }
      out[name] = String(el.value || '').trim();
    });
    return out;
  }
  // Multi-offer pages: cfg.offers[id] carries amount / termsVersion, as in pay.js staticOffer().
  function offer(id) {
    var o = (cfg.offers && cfg.offers[id]) || cfg;
    return { amount: o.amount, termsVersion: o.termsVersion || cfg.termsVersion };
  }
  function show(box, text) { box.textContent = text; box.hidden = false; }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    errBox.hidden = true; okBox.hidden = true;
    if (!form.reportValidity()) return;
    btn.disabled = true; btn.setAttribute('aria-busy', 'true'); btn.textContent = 'Préparation du devis…';
    var body = {
      action: 'quote',
      product: product(),
      email: form.elements.email.value.trim(),
      company: form.elements.company.value.trim(),
      siren: form.elements.siren.value.replace(/[\s.]/g, ''),
      addressLine1: form.elements.addressLine1.value.trim(),
      addressLine2: form.elements.addressLine2.value.trim(),
      postalCode: form.elements.postalCode.value.replace(/\s/g, ''),
      city: form.elements.city.value.trim(),
      buyerRef: form.elements.buyerRef.value.trim(),
      website: form.elements.website.value,
      termsAccepted: form.elements.terms.checked === true,
      termsVersion: offer(product()).termsVersion,
      expectedAmount: offer(product()).amount,
      attempt: attempt,
    };
    if (form.querySelector('[data-octave-extra]')) body.extra = extra();
    fetch(cfg.endpoint, { method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: JSON.stringify(body), redirect: 'follow' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) throw new Error(j.message || 'Devis impossible pour le moment.');
        show(okBox, j.paid
          ? 'Paiement déjà disponible sur votre compte : la commande est livrée à ' + j.email_masked + '.'
          : 'Devis ' + j.quote + ' envoyé à ' + j.email_masked + '. Il contient les coordonnées bancaires et la référence de virement ; pensez à vérifier vos courriers indésirables.');
        btn.textContent = 'Devis envoyé';
      })
      .catch(function (e) {
        show(errBox, (e && e.message) || 'Devis impossible pour le moment.');
        btn.disabled = false; btn.removeAttribute('aria-busy'); btn.textContent = 'Recevoir mon devis par e-mail';
      });
  });
})();
