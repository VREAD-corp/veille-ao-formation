/* octave-pay-gas: product page. Stripe "deferred intent" flow with the Payment Element:
 * mount the Payment Element AT ONCE (amount from the static build config, card only) -> on submit: browser
 * checks, elements.submit(), POST createIntent (text/plain: no CORS preflight; the server re-prices from its
 * catalog, refuses a stale expectedAmount and creates the card-only PaymentIntent) -> confirmPayment -> order page.
 * Stripe.js is loaded from https://js.stripe.com/v3/ (required by Stripe, never self-hosted).
 *
 * Static price (fast path): window.OCTAVE_PAY carries `amount` + `termsVersion` (one product) or
 * `offers: {id: {amount, termsVersion?}}` (several), plus `payLabel` ("Payer {prix} et …"). The Apps Script
 * catalog is then only read in the background (it also wakes the web app up): if it disagrees with the
 * displayed price or terms, the button stays disabled and the buyer is told to reload. Without a static
 * amount the page falls back to the catalog-first flow (slower).
 *
 * Optional, for pages that sell more than a file (all backward compatible):
 *   - several products on one page: radio inputs name="product" (value = catalog id); the amount of the
 *     Payment Element, the price and the button follow the checked one;
 *   - extra order fields: any input/select/textarea with a data-octave-extra attribute is sent in
 *     body.extra (checkboxes sharing a name -> array of checked values; data-octave-extra="bool" -> boolean);
 *   - an "octave-validate" event is dispatched on the form before the browser validity check, so a page
 *     script can set custom validity messages (e.g. "choose at least one region"). */
(function () {
  'use strict';
  var cfg = window.OCTAVE_PAY || {};
  var form = document.getElementById('octave-pay');
  var btn = document.getElementById('pay-button');
  var errBox = document.getElementById('pay-error');
  var PM_TYPES = ['card']; // must equal payment_method_types of the server-side PaymentIntent (Code.js)
  var attempt = randomId();
  var pending = null;
  var offers = {};
  var current = null;
  var ready = false;
  var busy = false;
  var blocked = null; // message when the displayed price / terms are stale

  function randomId() {
    var a = new Uint8Array(16);
    window.crypto.getRandomValues(a);
    return 'att_' + Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  /** SIREN: 9 digits + Luhn (same rule as the server; La Poste 356000000 is the documented exception). */
  function sirenOk(v) {
    if (!/^\d{9}$/.test(v) || v === '000000000') return false;
    if (v === '356000000') return true;
    var sum = 0;
    for (var i = 0; i < 9; i++) {
      var d = Number(v.charAt(8 - i));
      if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9; }
      sum += d;
    }
    return sum % 10 === 0;
  }
  window.OCTAVE_SIREN_OK = sirenOk;
  var sirenInput = form.elements.siren;
  function checkSiren() {
    var v = sirenInput.value.replace(/[\s.]/g, '');
    sirenInput.setCustomValidity(v && !sirenOk(v) ? 'SIREN invalide : vérifiez les 9 chiffres (contrôle de Luhn).' : '');
  }
  sirenInput.addEventListener('input', checkSiren);
  sirenInput.addEventListener('change', checkSiren);

  /* Loading placeholder + spinner (CSS injected once; every checkout CSP allows inline styles). */
  var css = document.createElement('style');
  css.textContent = '.octave-spinner{display:inline-block;width:1em;height:1em;margin-right:.5em;vertical-align:-.15em;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:octave-spin .7s linear infinite}@keyframes octave-spin{to{transform:rotate(360deg)}}@media (prefers-reduced-motion:reduce){.octave-spinner{animation-duration:2s}}#payment-loading{margin:.8em 0}#pay-button[aria-busy=true]{cursor:progress}';
  document.head.appendChild(css);
  var peBox = document.getElementById('payment-element');
  var loading = document.getElementById('payment-loading');
  if (!loading) {
    loading = document.createElement('p');
    loading.id = 'payment-loading';
    loading.className = 'small muted';
    loading.setAttribute('role', 'status');
    loading.textContent = 'Chargement du paiement sécurisé…';
    peBox.parentNode.insertBefore(loading, peBox);
  }

  function formatPrice(cents) {
    var s = cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2).replace('.', ',');
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' €';
  }
  function payLabel(o) { return (o.payLabel || 'Payer {prix}').replace('{prix}', formatPrice(o.amount)); }
  function setButton() {
    if (busy) return;
    btn.textContent = payLabel(offers[current]);
    btn.disabled = !ready || !!blocked;
  }
  function setBusy(on) {
    busy = on;
    if (on) {
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      btn.textContent = '';
      var sp = document.createElement('span');
      sp.className = 'octave-spinner';
      sp.setAttribute('aria-hidden', 'true');
      btn.appendChild(sp);
      btn.appendChild(document.createTextNode('Paiement en cours…'));
    } else {
      btn.removeAttribute('aria-busy');
      setButton();
    }
  }
  function showError(msg) { errBox.textContent = msg; errBox.hidden = false; setBusy(false); }
  function block(msg) { blocked = msg; errBox.textContent = msg; errBox.hidden = false; btn.disabled = true; }

  function call(body) {
    return fetch(cfg.endpoint, { method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: JSON.stringify(body), redirect: 'follow' })
      .then(function (r) { return r.json(); });
  }
  function fetchCatalog(id) {
    return fetch(cfg.endpoint + '?action=catalog&product=' + encodeURIComponent(id), { redirect: 'follow' })
      .then(function (r) { return r.json(); })
      .then(function (c) { if (!c.ok) throw new Error(c.message || 'Produit indisponible.'); return c; });
  }

  var productInputs = Array.prototype.slice.call(form.querySelectorAll('input[name="product"]'));
  function selectedProduct() {
    if (!productInputs.length) return cfg.product;
    var checked = productInputs.filter(function (i) { return i.checked; })[0];
    return (checked || productInputs[0]).value;
  }
  function extraFields() {
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
  /** Static offer from the build config, or null (then the catalog decides). */
  function staticOffer(id) {
    var o = (cfg.offers && cfg.offers[id]) || (id === cfg.product && typeof cfg.amount === 'number' ? cfg : null);
    var tv = o && (o.termsVersion || cfg.termsVersion);
    if (!o || typeof o.amount !== 'number' || !(o.amount > 0) || !tv) return null;
    return { amount: o.amount, currency: o.currency || cfg.currency || 'eur', termsVersion: tv, payLabel: o.payLabel || cfg.payLabel };
  }

  var termsLink = document.getElementById('terms-link');
  if (cfg.termsUrl) termsLink.href = cfg.termsUrl;
  if (typeof window.Stripe !== 'function') { loading.hidden = true; block('Le module de paiement Stripe n’a pas pu être chargé.'); return; }

  var ids = productInputs.length ? productInputs.map(function (i) { return i.value; }) : [cfg.product];
  ids.forEach(function (id) { offers[id] = staticOffer(id); });
  if (ids.every(function (id) { return offers[id]; })) {
    start();
    // Background check (never blocks the mount): the server catalog is the price authority.
    ids.forEach(function (id) {
      fetchCatalog(id).then(function (c) {
        var o = offers[id];
        if (!cfg.termsUrl && c.termsUrl) termsLink.href = c.termsUrl;
        if (c.amount !== o.amount || c.currency !== o.currency) block('Le prix affiché n’est plus à jour (prix actuel : ' + c.label + '). Rechargez la page avant de payer.');
        else if (c.termsVersion && c.termsVersion !== o.termsVersion) block('Les conditions générales de vente ont changé : rechargez la page avant de payer.');
      }).catch(function () { /* the createIntent call re-checks everything */ });
    });
  } else {
    // Legacy pages without a static price: catalog first (slower).
    Promise.all(ids.map(fetchCatalog))
      .then(function (list) {
        list.forEach(function (c, i) { offers[ids[i]] = { amount: c.amount, currency: c.currency, termsVersion: c.termsVersion, payLabel: cfg.payLabel }; });
        var c = list[ids.indexOf(selectedProduct())];
        var nameEl = document.getElementById('product-name');
        var priceEl = document.getElementById('product-price');
        if (nameEl && nameEl.getAttribute('data-static') !== '1') { nameEl.textContent = c.name; document.title = c.name + ' – commande'; }
        if (priceEl) priceEl.textContent = c.label;
        productInputs.forEach(function (inp, i) {
          var lbl = form.querySelector('[data-price-for="' + inp.value + '"]');
          if (lbl) lbl.textContent = list[i].label;
        });
        if (!cfg.termsUrl && c.termsUrl) termsLink.href = c.termsUrl;
        start();
      })
      .catch(function (e) { loading.hidden = true; block(e.message || 'Service indisponible.'); });
  }

  function start() {
    current = selectedProduct();
    var o = offers[current];
    var stripe = window.Stripe(cfg.publishableKey, { locale: 'fr' });
    var elements = stripe.elements({ mode: 'payment', amount: o.amount, currency: o.currency, paymentMethodTypes: PM_TYPES, locale: 'fr' });
    var pe = elements.create('payment', { layout: 'tabs' });
    pe.on('ready', function () { ready = true; loading.hidden = true; form.setAttribute('data-ready', '1'); setButton(); });
    pe.on('loaderror', function () { loading.hidden = true; block('Le module de paiement n’a pas pu être chargé : rechargez la page.'); });
    pe.mount('#payment-element');
    setButton();
    productInputs.forEach(function (i) {
      i.addEventListener('change', function () {
        current = selectedProduct();
        var no = offers[current];
        elements.update({ amount: no.amount, currency: no.currency });
        var priceEl = document.getElementById('product-price');
        var lbl = form.querySelector('[data-price-for="' + current + '"]');
        if (priceEl && lbl) priceEl.textContent = lbl.textContent;
        setButton();
      });
    });
    form.addEventListener('submit', function (e) { e.preventDefault(); if (!busy) submit(stripe, elements); });
  }

  function submit(stripe, elements) {
    if (blocked) { errBox.textContent = blocked; errBox.hidden = false; return; }
    errBox.hidden = true;
    checkSiren();
    form.dispatchEvent(new Event('octave-validate'));
    if (!form.reportValidity()) return;
    setBusy(true);
    var o = offers[current];
    var body = {
      action: 'createIntent',
      product: current,
      email: form.elements.email.value.trim(),
      company: form.elements.company.value.trim(),
      siren: form.elements.siren.value.replace(/[\s.]/g, ''),
      addressLine1: form.elements.addressLine1.value.trim(),
      addressLine2: form.elements.addressLine2.value.trim(),
      postalCode: form.elements.postalCode.value.replace(/\s/g, ''),
      city: form.elements.city.value.trim(),
      termsAccepted: form.elements.terms.checked === true,
      termsVersion: o.termsVersion,
      expectedAmount: o.amount,
      attempt: attempt
    };
    if (form.querySelector('[data-octave-extra]')) body.extra = extraFields();
    var key = JSON.stringify(body);
    elements.submit().then(function (r) {
      if (r.error) throw r.error;
      if (pending && pending.key === key) return pending;
      return call(body).then(function (j) {
        if (!j.ok || !j.client_secret) throw new Error(j.message || 'Commande impossible.');
        // The server is the price authority: never confirm an amount other than the one displayed.
        if (j.amount !== o.amount || (j.currency && j.currency !== o.currency)) {
          block('Le prix a changé (prix actuel : ' + (j.label || formatPrice(j.amount)) + ') : aucun paiement n’a été effectué. Rechargez la page.');
          throw null;
        }
        pending = { key: key, clientSecret: j.client_secret };
        return pending;
      });
    }).then(function (p) {
      var returnUrl = new URL(cfg.orderPage || 'order.html', window.location.href).toString();
      return stripe.confirmPayment({ elements: elements, clientSecret: p.clientSecret, confirmParams: { return_url: returnUrl } });
    }).then(function (r) {
      if (r && r.error) showError(r.error.message || 'Paiement refusé.');
    }).catch(function (err) {
      if (err === null) { busy = false; btn.removeAttribute('aria-busy'); btn.textContent = payLabel(offers[current]); btn.disabled = true; return; }
      showError((err && err.message) || 'Paiement impossible.');
    });
  }
})();
