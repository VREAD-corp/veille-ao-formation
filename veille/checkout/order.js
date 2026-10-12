/* octave-pay-gas: order page (Stripe return_url). Calls the Apps Script `fulfil` action, which
 * re-checks the PaymentIntent with Stripe and e-mails the ZIP once. If this tab is closed, the
 * 10-minute trigger on the Apps Script side sends the file anyway. Products that are not a file
 * (subscription, audit) get their confirmation sentence from the server (`done_text`). */
(function () {
  'use strict';
  var cfg = window.OCTAVE_PAY || {};
  var statusEl = document.getElementById('order-status');
  var help = document.getElementById('order-help');
  var q = new URLSearchParams(window.location.search);
  var pi = q.get('payment_intent');
  var secret = q.get('payment_intent_client_secret');
  var redirectStatus = q.get('redirect_status');
  // Keep the client secret out of history / bookmarks once read.
  if (secret && window.history.replaceState) window.history.replaceState(null, '', window.location.pathname + '?payment_intent=' + encodeURIComponent(pi || ''));

  function set(text, cls, more) { statusEl.textContent = text; statusEl.className = 'status ' + (cls || ''); help.textContent = more || ''; }
  var LATER = cfg.laterText || 'Si vous fermez cette page, le fichier vous sera tout de même envoyé automatiquement dans les 10 minutes.';

  if (!pi || !secret) { set('Lien de commande incomplet.', 'err', LATER); return; }
  if (redirectStatus && redirectStatus !== 'succeeded') { set('Le paiement n’a pas abouti. Aucun montant n’a été débité.', 'err', 'Vous pouvez revenir à la page précédente pour réessayer.'); return; }

  var tries = 0;
  function attempt() {
    tries++;
    fetch(cfg.endpoint, { method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: JSON.stringify({ action: 'fulfil', payment_intent: pi, client_secret: secret }), redirect: 'follow' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.ok) {
          statusEl.setAttribute('data-fulfilled', '1');
          set(j.done_text || ('Paiement confirmé. Fichier envoyé à ' + j.email_masked + '.'), 'ok', 'Commande ' + j.order_id + '. Pensez à vérifier vos courriers indésirables.');
        } else if (j.error === 'not_paid' && tries < 6) {
          set('Paiement en cours de confirmation…', '', LATER);
          setTimeout(attempt, 2000 * tries);
        } else {
          set(j.error === 'not_paid' ? 'Paiement en cours de confirmation.' : (j.message || 'Envoi impossible pour le moment.'), j.error === 'not_paid' ? '' : 'err', LATER);
        }
      })
      .catch(function () {
        if (tries < 4) { setTimeout(attempt, 2000 * tries); return; }
        set('Le service est momentanément injoignable.', 'err', LATER);
      });
  }
  attempt();
})();
