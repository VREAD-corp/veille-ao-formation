/* Veille AO (secteurs), /alertes/: load Stripe.js and pay.js only when the visitor shows an intent to order.
 *
 * Stripe.js pulls about 4 MB (Stripe, hCaptcha, Google Pay) and third-party cookies. Loading it on page load
 * put all of that on every reader of the page. It is now loaded on the first of:
 *   - focus, click, tap or key press inside the order form (#octave-pay);
 *   - a click on a link to the order section (#commander-h: hero, plan cards, sticky bar);
 *   - arriving with #commander-h in the URL, or with ?devis=… (card alternative printed on a quote).
 * pay.js (verbatim copy of octave-checkout gas/web/pay.js) runs once Stripe.js is there and mounts the
 * Payment Element as before. Until then the pay button stays disabled, so the form cannot be submitted. */
(function () {
  'use strict';
  var me = document.currentScript;
  var payScript = me && me.getAttribute('data-pay');
  var form = document.getElementById('octave-pay');
  if (!form || !payScript) return;
  var loading = document.getElementById('payment-loading');
  var waitText = 'Le module de paiement par carte (Stripe) se charge quand vous commencez à remplir ce formulaire.';
  var loadText = loading ? loading.textContent : '';
  if (loading) loading.textContent = waitText;
  var started = false;
  var events = ['focusin', 'pointerdown', 'touchstart', 'keydown'];

  function addScript(src, done) {
    var s = document.createElement('script');
    s.src = src;
    s.async = false;
    s.onload = s.onerror = done;
    document.body.appendChild(s);
  }
  function start() {
    if (started) return;
    started = true;
    events.forEach(function (ev) { form.removeEventListener(ev, start, true); });
    document.removeEventListener('click', onLinkClick, true);
    window.removeEventListener('hashchange', onHash);
    if (loading) loading.textContent = loadText || 'Chargement du paiement sécurisé…';
    // On error pay.js still runs and shows its own "module de paiement Stripe n'a pas pu être chargé" message.
    addScript('https://js.stripe.com/v3/', function () {
      addScript(payScript, function () {
        // A plan picked before pay.js was listening: let it update the price line and the Element amount.
        var checked = form.querySelector('input[name="product"]:checked');
        if (checked && typeof Event === 'function') checked.dispatchEvent(new Event('change'));
      });
    });
  }
  function onLinkClick(e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (a && a.getAttribute('href') === '#commander-h') start();
  }
  function onHash() { if (location.hash === '#commander-h') start(); }

  // Arriving on the order section: start once the deferred scripts have run (cro.js draws the A/B arm and
  // checkout/xp-price.js switches the 12-month radio to it), so pay.js never reads the arm-a radio of an arm-b tab.
  if (location.hash === '#commander-h' || /[?&]devis=/.test(location.search)) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
    return;
  }
  events.forEach(function (ev) { form.addEventListener(ev, start, { capture: true, passive: true }); });
  document.addEventListener('click', onLinkClick, true);
  window.addEventListener('hashchange', onHash);
})();
