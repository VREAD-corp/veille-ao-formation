/* Veille AO Formation subscription page: browser-side checks of the alert criteria (the server checks them
 * again). Runs on the "octave-validate" event that pay.js dispatches before the form validity check. */
(function () {
  'use strict';
  var form = document.getElementById('octave-pay');
  if (!form) return;
  var regions = Array.prototype.slice.call(form.querySelectorAll('input[name="regions"]'));
  var keywords = form.elements.keywords;
  function check() {
    var any = regions.some(function (r) { return r.checked; });
    regions[0].setCustomValidity(any ? '' : 'Choisissez au moins une région.');
    var kw = keywords.value.split(/[,;\n|]+/).map(function (k) { return k.trim(); }).filter(Boolean);
    var bad = kw.filter(function (k) { return k.length < 2 || k.length > 40 || /[<>"]/.test(k); });
    keywords.setCustomValidity(kw.length > 10 ? '10 mots-clés au maximum (' + kw.length + ' indiqués).' : bad.length ? 'Mot-clé invalide : « ' + bad[0].slice(0, 40) + ' » (2 à 40 caractères, sans < > ").' : '');
  }
  function setAll(v) { regions.forEach(function (r) { r.checked = v; }); check(); }
  var all = document.getElementById('regions-all');
  var none = document.getElementById('regions-none');
  if (all) all.addEventListener('click', function () { setAll(true); });
  if (none) none.addEventListener('click', function () { setAll(false); });
  regions.forEach(function (r) { r.addEventListener('change', check); });
  keywords.addEventListener('input', check);
  form.addEventListener('octave-validate', check);
  check();
})();
