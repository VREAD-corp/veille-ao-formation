// Alerts page: volume estimate per region (read-only, from the numbers baked into the page at build time)
// and the mobile sticky bar. Never writes to the order form: it only listens to its region checkboxes.
(function () {
  "use strict";
  var box = document.getElementById("volume");
  if (box) {
    var data;
    try { data = JSON.parse(box.getAttribute("data-volume")); } catch (err) { data = null; }
    var select = document.getElementById("vol-region");
    var out = document.getElementById("vol-out");
    var formOut = document.getElementById("vol-form");
    if (data && select && out) {
      var bySlug = {};
      data.regions.forEach(function (r, i) { bySlug[r[1]] = i; });
      var plural = function (n, one, many) { return n > 1 ? many : one; };
      // Notices of the last 30 days located in at least one of the given regions, plus the national ones.
      var count = function (idx) {
        var set = {};
        idx.forEach(function (i) { set[i] = true; });
        var n = data.nat;
        data.items.forEach(function (regs) {
          for (var k = 0; k < regs.length; k++) { if (set[regs[k]]) { n++; return; } }
        });
        return n;
      };
      var perWeek = function (n) { return Math.round(n * 7 / 30); };
      var show = function () {
        var i = bySlug[select.value];
        if (i === undefined) return;
        var n = count([i]);
        var w = perWeek(n);
        out.innerHTML = "";
        var strong = document.createElement("strong");
        strong.textContent = "~" + n + " " + plural(n, "avis", "avis");
        out.appendChild(strong);
        out.appendChild(document.createTextNode(
          " ces 30 derniers jours en " + data.regions[i][0] + " (dont " + data.nat + " " + plural(data.nat, "national", "nationaux") + ")" +
          (w >= 1 ? ", soit environ " + w + " par semaine." : ".") +
          (n < 8 ? " C'est peu : le service est plus utile si vous suivez plusieurs régions." : "")));
      };
      select.addEventListener("change", show);
      var q = new URLSearchParams(location.search).get("region");
      if (q && bySlug[q] !== undefined) { select.value = q; show(); }

      // Optional: estimate for the regions ticked in the order form (read only).
      // Card form only (the first one): the optional quote form below repeats the same checkboxes.
      var boxes = Array.prototype.slice.call(document.querySelectorAll('input[name="regions"]'));
      boxes = boxes.filter(function (b) { return b.form === boxes[0].form; });
      if (formOut && boxes.length) {
        var update = function () {
          var idx = boxes.filter(function (b) { return b.checked && bySlug[b.value] !== undefined; })
            .map(function (b) { return bySlug[b.value]; });
          if (!idx.length) { formOut.hidden = true; return; }
          var n = count(idx);
          formOut.hidden = false;
          formOut.textContent = "Pour les " + idx.length + " " + plural(idx.length, "région cochée", "régions cochées") +
            " dans le formulaire : ~" + n + " avis ces 30 derniers jours, avant filtrage par mots-clés.";
        };
        boxes.forEach(function (b) { b.addEventListener("change", update); });
        ["regions-all", "regions-none"].forEach(function (id) {
          var btn = document.getElementById(id);
          if (btn) btn.addEventListener("click", function () { setTimeout(update, 0); });
        });
        update();
      }
    }
  }

  // Hide the sticky "Commander" bar while the order section is on screen.
  var bar = document.getElementById("sticky-cta");
  var section = document.querySelector(".checkout");
  if (bar && section && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      bar.classList.toggle("is-hidden", entries[0].isIntersecting);
    }).observe(section);
  }
})();
