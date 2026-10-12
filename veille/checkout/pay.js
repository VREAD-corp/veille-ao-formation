/* octave-pay-gas: product page. Stripe "deferred intent" flow with the Payment Element:
 * catalog (price from the Apps Script server) -> mount Payment Element -> on submit: browser checks,
 * elements.submit(), POST createIntent (text/plain: no CORS preflight) -> confirmPayment -> order page.
 * Stripe.js is loaded from https://js.stripe.com/v3/ (required by Stripe, never self-hosted).
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
  var attempt = randomId();
  var pending = null;
  var catalogs = {};
  var current = null;

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

  function showError(msg) { errBox.textContent = msg; errBox.hidden = false; btn.disabled = false; btn.removeAttribute('aria-busy'); }
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
  function buttonLabel(c) { return 'Commander avec obligation de paiement – ' + c.label.split(' – ').pop().replace(/ \(.*\)$/, ''); }

  var termsLink = document.getElementById('terms-link');
  if (cfg.termsUrl) termsLink.href = cfg.termsUrl;
  if (typeof window.Stripe !== 'function') { showError('Le module de paiement Stripe n’a pas pu être chargé.'); btn.disabled = true; return; }

  var ids = productInputs.length ? productInputs.map(function (i) { return i.value; }) : [cfg.product];
  Promise.all(ids.map(fetchCatalog))
    .then(function (list) {
      list.forEach(function (c, i) { catalogs[ids[i]] = c; });
      current = selectedProduct();
      var c = catalogs[current];
      var nameEl = document.getElementById('product-name');
      var priceEl = document.getElementById('product-price');
      if (nameEl && nameEl.getAttribute('data-static') !== '1') { nameEl.textContent = c.name; document.title = c.name + ' – commande'; }
      if (priceEl) priceEl.textContent = c.label;
      productInputs.forEach(function (i) {
        var lbl = form.querySelector('[data-price-for="' + i.value + '"]');
        if (lbl && catalogs[i.value]) lbl.textContent = catalogs[i.value].label;
      });
      if (!cfg.termsUrl && c.termsUrl) termsLink.href = c.termsUrl;
      var stripe = window.Stripe(cfg.publishableKey, { locale: 'fr' });
      var elements = stripe.elements({ mode: 'payment', amount: c.amount, currency: c.currency, locale: 'fr' });
      var pe = elements.create('payment', { layout: 'tabs' });
      pe.on('ready', function () { btn.disabled = false; form.setAttribute('data-ready', '1'); });
      pe.mount('#payment-element');
      btn.textContent = buttonLabel(c);
      productInputs.forEach(function (i) {
        i.addEventListener('change', function () {
          current = selectedProduct();
          var nc = catalogs[current];
          elements.update({ amount: nc.amount, currency: nc.currency });
          if (priceEl) priceEl.textContent = nc.label;
          btn.textContent = buttonLabel(nc);
        });
      });
      form.addEventListener('submit', function (e) { e.preventDefault(); submit(stripe, elements); });
    })
    .catch(function (e) { showError(e.message || 'Service indisponible.'); btn.disabled = true; });

  function submit(stripe, elements) {
    errBox.hidden = true;
    checkSiren();
    form.dispatchEvent(new Event('octave-validate'));
    if (!form.reportValidity()) return;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    var c = catalogs[current];
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
      termsVersion: c.termsVersion,
      attempt: attempt
    };
    if (form.querySelector('[data-octave-extra]')) body.extra = extraFields();
    var key = JSON.stringify(body);
    elements.submit().then(function (r) {
      if (r.error) throw r.error;
      if (pending && pending.key === key) return pending;
      return call(body).then(function (j) {
        if (!j.ok || !j.client_secret) throw new Error(j.message || 'Commande impossible.');
        pending = { key: key, clientSecret: j.client_secret };
        return pending;
      });
    }).then(function (p) {
      var returnUrl = new URL(cfg.orderPage || 'order.html', window.location.href).toString();
      return stripe.confirmPayment({ elements: elements, clientSecret: p.clientSecret, confirmParams: { return_url: returnUrl } });
    }).then(function (r) {
      if (r && r.error) showError(r.error.message || 'Paiement refusé.');
    }).catch(function (err) { showError((err && err.message) || 'Paiement impossible.'); });
  }
})();
