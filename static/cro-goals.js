/* Veille AO Formation: conversion goals for cro.js (octave-checkout gas/web/cro.js, loaded just before).
 * Only successes the site already detects: the order form submitted (#octave-pay, same as mesure.js
 * checkout_start), a free format downloaded (data-mesure="free_download") and, on /alertes/commande.html,
 * the subscription confirmed (order.js sets data-fulfilled="1" on #order-status). A goal only counts for an
 * A/B experiment the tab was exposed to (none runs today); nothing typed is ever read. */
(function (d) {
  'use strict';
  var goal = function (m) { if (window.octaveCro) window.octaveCro.goal(m); };
  d.addEventListener('submit', function (e) { if (e.target && e.target.id === 'octave-pay') goal('checkout'); }, true);
  d.addEventListener('click', function (e) {
    var t = e.target && e.target.closest ? e.target.closest('[data-mesure="free_download"]') : null;
    if (t) goal('download');
  }, true);
  var status = d.getElementById('order-status');
  if (!status) return;
  var done = false, check = function () {
    if (!done && status.getAttribute('data-fulfilled') === '1') { done = true; goal('paid'); }
  };
  check();
  if (window.MutationObserver) new MutationObserver(check).observe(status, { attributes: true, attributeFilter: ['data-fulfilled'] });
})(document);
