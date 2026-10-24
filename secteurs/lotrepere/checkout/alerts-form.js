/* Veille AO (secteurs) subscription page: browser-side checks of the alert criteria (the server checks them again)
 * and preselection from the link that brought the visitor (?niche=…&region=…). Bound to the card form
 * (#octave-pay; also runs on the "octave-validate" event that pay.js dispatches before the form validity check)
 * and to the quote form (#octave-devis, ids suffixed "-devis"), whose devis.js calls reportValidity() directly:
 * the custom validity is kept up to date on every change. */
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  function bind(form, suffix) {
    if (!form) return;
    var regions = Array.prototype.slice.call(form.querySelectorAll('input[name="regions"]'));
    var niches = Array.prototype.slice.call(form.querySelectorAll('input[name="niches"]'));
    var keywords = form.elements.keywords;
    if (!regions.length || !niches.length || !keywords) return;
    var n = params.get('niche');
    if (n && niches.some(function (x) { return x.value === n; })) niches.forEach(function (x) { x.checked = x.value === n; });
    var r = params.get('region');
    regions.forEach(function (x) { if (x.value === r) x.checked = true; });
    function check() {
      niches[0].setCustomValidity(niches.some(function (x) { return x.checked; }) ? '' : 'Choisissez au moins un métier.');
      regions[0].setCustomValidity(regions.some(function (x) { return x.checked; }) ? '' : 'Choisissez au moins une région.');
      var kw = keywords.value.split(/[,;\n|]+/).map(function (k) { return k.trim(); }).filter(Boolean);
      var bad = kw.filter(function (k) { return k.length < 2 || k.length > 40 || /[<>"]/.test(k); });
      keywords.setCustomValidity(kw.length > 10 ? '10 mots-clés au maximum (' + kw.length + ' indiqués).' : bad.length ? 'Mot-clé invalide : « ' + bad[0].slice(0, 40) + ' » (2 à 40 caractères, sans < > ").' : '');
    }
    function setAll(v) { regions.forEach(function (x) { x.checked = v; }); check(); }
    var all = document.getElementById('regions-all' + suffix);
    var none = document.getElementById('regions-none' + suffix);
    if (all) all.addEventListener('click', function () { setAll(true); });
    if (none) none.addEventListener('click', function () { setAll(false); });
    regions.concat(niches).forEach(function (x) { x.addEventListener('change', check); });
    keywords.addEventListener('input', check);
    form.addEventListener('octave-validate', check);
    check();
  }
  bind(document.getElementById('octave-pay'), '');
  bind(document.getElementById('octave-devis'), '-devis');
})();
