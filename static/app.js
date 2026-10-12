// Client-side filters for the open-tender list, "voir plus" paging and the alert call-to-action cards.
// No tracking, no storage beyond the URL.
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
  var cards = Array.prototype.slice.call(document.querySelectorAll(".list > .card"));
  var list = document.querySelector(".list");
  var section = document.getElementById("liste");
  var PAGE = 20;
  var limit = PAGE;
  var volume = {};
  try { volume = JSON.parse(section.getAttribute("data-volume")) || {}; } catch (err) { volume = {}; }
  var days = section ? section.getAttribute("data-days") : "30";
  var tpl = document.getElementById("cta-tpl");
  var more = null;
  if (list && cards.length > PAGE) {
    more = document.createElement("button");
    more.type = "button";
    more.className = "btn btn-ghost more";
    more.addEventListener("click", function () { limit += PAGE; apply(true); });
    list.parentNode.insertBefore(more, list.nextSibling);
  }

  // Call-to-action card after the 5th shown card, then every 20 cards, tailored to the region filter.
  function placeCtas(shownCards) {
    Array.prototype.slice.call(document.querySelectorAll(".list > .inline-cta")).forEach(function (n) { n.remove(); });
    if (!tpl || !list) return;
    var r = region.value;
    var spots = [];
    for (var at = 5; at <= shownCards.length; at += 20) spots.push(at);
    if (!spots.length && shownCards.length) spots.push(shownCards.length);
    spots.forEach(function (at) {
      var node = tpl.content.firstElementChild.cloneNode(true);
      if (r && volume[r]) {
        node.querySelector(".ic-title").textContent = "Les appels d'offres formation en " + r + ", chaque matin dans votre boîte";
        node.querySelector(".ic-text").textContent = "~" + volume[r][0] + " avis publiés ces " + days +
          " derniers jours en " + r + " (avis nationaux compris). Recevez seulement les nouveaux, filtrés par vos mots-clés.";
        var a = node.querySelector("a");
        a.href = a.getAttribute("href").split("?")[0] + "?region=" + encodeURIComponent(volume[r][1]);
        a.textContent = "Recevoir les AO formation en " + r + " chaque matin →";
      }
      list.insertBefore(node, shownCards[at - 1].nextSibling);
    });
  }

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

  function apply(keepLimit) {
    if (keepLimit !== true) limit = PAGE;
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
      if (ok) shown++;
      c.hidden = !ok || shown > limit;
    });
    var visible = cards.filter(function (c) { return !c.hidden; });
    placeCtas(visible);
    if (more) {
      var rest = shown - visible.length;
      more.hidden = rest <= 0;
      more.textContent = "Voir plus d'annonces (" + rest + " de plus)";
    }
    count.textContent = shown + (shown > 1 ? " appels d'offres correspondent" : " appel d'offres correspond") +
      " sur " + cards.length + (shown > visible.length ? ", " + visible.length + " affichés." : ".");
    writeUrl();
  }

  readUrl();
  [q, region, deadline, national].forEach(function (el) {
    el.addEventListener(el === q ? "input" : "change", function () { apply(); });
  });
  apply();
})();
