// Client-side filters for the open-tender list. No tracking, no storage beyond the URL.
(function () {
  "use strict";
  var panel = document.getElementById("filtres");
  if (!panel) return;
  panel.hidden = false;

  var q = document.getElementById("q");
  var region = document.getElementById("region");
  var deadline = document.getElementById("deadline");
  var national = document.getElementById("national");
  var count = document.getElementById("count");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));

  function fold(s) {
    return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function daysLeft(iso) {
    if (!iso) return null;
    var d = new Date(iso.slice(0, 10) + "T00:00:00");
    var t = new Date();
    t.setHours(0, 0, 0, 0);
    return Math.round((d - t) / 86400000);
  }

  // Keep "J-n" badges accurate between two builds and hide tenders that closed since.
  function closed(iso) {
    if (!iso) return false;
    return new Date(iso + ":00") < new Date();
  }

  cards.forEach(function (c) {
    var b = c.querySelector(".badge:not(.soft)");
    var dl = c.getAttribute("data-deadline");
    var n = daysLeft(dl);
    if (b && n !== null && !b.classList.contains("closed")) {
      if (closed(dl)) {
        b.textContent = "Clôturé";
        b.className = "badge closed";
        c.setAttribute("data-closed", "1");
      } else {
        b.textContent = "J-" + n;
        b.className = n <= 7 ? "badge urgent" : "badge";
      }
    }
  });

  function readUrl() {
    var p = new URLSearchParams(location.search);
    if (p.get("q")) q.value = p.get("q");
    if (p.get("region")) region.value = p.get("region");
    if (p.get("echeance")) deadline.value = p.get("echeance");
    if (p.get("national") === "0") national.checked = false;
  }

  function writeUrl() {
    var p = new URLSearchParams();
    if (q.value.trim()) p.set("q", q.value.trim());
    if (region.value) p.set("region", region.value);
    if (deadline.value) p.set("echeance", deadline.value);
    if (!national.checked) p.set("national", "0");
    var s = p.toString();
    history.replaceState(null, "", s ? "?" + s + location.hash : location.pathname + location.hash);
  }

  function apply() {
    var words = fold(q.value).split(/\s+/).filter(Boolean);
    var r = region.value;
    var dv = deadline.value;
    var shown = 0;
    cards.forEach(function (c) {
      var ok = c.getAttribute("data-closed") !== "1";
      var text = c.getAttribute("data-search") || "";
      for (var i = 0; ok && i < words.length; i++) {
        if (text.indexOf(words[i]) === -1) ok = false;
      }
      var isNational = c.getAttribute("data-national") === "1";
      if (ok && isNational && !national.checked) ok = false;
      if (ok && r) {
        var regs = (c.getAttribute("data-regions") || "").split("|");
        if (regs.indexOf(r) === -1 && !isNational) ok = false;
      }
      if (ok && dv) {
        var n = daysLeft(c.getAttribute("data-deadline"));
        var lim = parseInt(dv.slice(2), 10);
        if (n === null) ok = false;
        else if (dv.indexOf("lt") === 0 && n > lim) ok = false;
        else if (dv.indexOf("gt") === 0 && n < lim) ok = false;
      }
      c.hidden = !ok;
      if (ok) shown++;
    });
    count.textContent = shown + (shown > 1 ? " appels d'offres affichés" : " appel d'offres affiché") +
      " sur " + cards.length + ".";
    writeUrl();
  }

  readUrl();
  [q, region, deadline, national].forEach(function (el) {
    el.addEventListener(el === q ? "input" : "change", apply);
  });
  apply();
})();
