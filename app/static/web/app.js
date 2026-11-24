// Comportamentos pequenos das telas. Tudo funciona sem JS; isto só poupa digitação.
// Menus, seleção em tabelas e confirmações ficam no próprio template com Alpine; filtros usam HTMX.
(function () {
  "use strict";

  // --- Tema claro/escuro (o <head> já aplicou o tema salvo antes de pintar) -----------------------
  document.querySelectorAll("[data-alternar-tema]").forEach(function (botao) {
    botao.addEventListener("click", function () {
      var escuro = document.documentElement.classList.toggle("dark");
      try { localStorage.setItem("tema", escuro ? "escuro" : "claro"); } catch (e) { /* navegação privada */ }
    });
  });

  // --- Formulário de simulação ------------------------------------------------------------------
  var form = document.getElementById("form-simulacao");
  if (!form) return;
  var catalogo = JSON.parse(document.getElementById("catalogo-json").textContent || "{}");
  var itens = document.getElementById("itens");
  var total = form.querySelector('input[name="itens-TOTAL_FORMS"]');
  var simples = form.dataset.simples === "1";

  function preencherDoCatalogo(select) {
    var dados = catalogo[select.value];
    if (!dados) return;
    var linha = select.closest("[data-linha]");
    ["ncm", "nbs", "cst", "c_class_trib", "c_ativ_sn"].forEach(function (campo) {
      var input = linha.querySelector('[name$="-' + campo + '"]');
      if (input) input.value = dados[campo] || "";
    });
  }

  itens.addEventListener("change", function (evento) {
    if (evento.target.matches(".campo-catalogo")) preencherDoCatalogo(evento.target);
  });
  itens.addEventListener("click", function (evento) {
    if (!evento.target.matches("[data-remover]")) return;
    var linha = evento.target.closest("[data-linha]");
    linha.querySelectorAll("input, select").forEach(function (c) { c.value = ""; });
    if (itens.querySelectorAll("[data-linha]").length > 1) linha.remove();
  });
  document.getElementById("adicionar-item").addEventListener("click", function () {
    var indice = parseInt(total.value, 10);
    var html = document.getElementById("modelo-item").innerHTML.replace(/__prefix__/g, String(indice));
    itens.insertAdjacentHTML("beforeend", html);
    total.value = String(indice + 1);
  });

  var campoData = form.querySelector('[name="data"]');

  if (simples) {
    var tpDoc = form.querySelector('[name="tp_doc"]');
    var nfse = function () { form.dataset.nfse = tpDoc.value === "91" ? "1" : "0"; };
    tpDoc.addEventListener("change", nfse);
    nfse();
    return;
  }

  // Regime regular: a partir de 2027 propõe as alíquotas da tabela do Tributo360 (D2).
  var anoAliquotas = parseInt(form.dataset.anoAliquotas, 10);
  var campos = document.getElementById("aliquotas-campos");
  var aviso2026 = document.getElementById("aliquotas-2026");
  var status = document.getElementById("aliquotas-status");
  var fontes = document.getElementById("aliquotas-fontes");
  var campoMunicipio = form.querySelector('[name="municipio"]');
  var mapa = { cbs: "cbs", ibs_estadual: "ibs_estadual", ibs_municipal: "ibs_municipal" };

  function consultar(sobrescrever) {
    var data = campoData.value;
    var exige = data && parseInt(data.slice(0, 4), 10) >= anoAliquotas;
    campos.hidden = !exige;
    aviso2026.hidden = !!exige;
    if (!exige) return;
    var municipio = (campoMunicipio && campoMunicipio.value) || form.dataset.municipio;
    status.textContent = "consultando a tabela...";
    fetch("/api/aliquotas-nominais/vigentes/?data=" + encodeURIComponent(data) + "&municipio=" + encodeURIComponent(municipio),
      { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (corpo) {
        var linhas = [];
        Object.keys(mapa).forEach(function (campo) {
          var registro = (corpo.aliquotas || {})[campo];
          var input = form.querySelector('[name="' + campo + '"]');
          if (registro) {
            if (sobrescrever || !input.value) input.value = String(registro.aliquota).replace(".", ",");
            linhas.push(input.labels[0].textContent + ": " + registro.fonte);
          } else {
            linhas.push(input.labels[0].textContent + ": sem valor na tabela, informe");
          }
        });
        fontes.textContent = "Fonte · " + linhas.join(" · ");
        status.textContent = "tabela do Tributo360 para " + data.split("-").reverse().join("/");
      })
      .catch(function () { status.textContent = "não consegui consultar a tabela; informe as alíquotas"; });
  }

  campoData.addEventListener("change", function () { consultar(true); });
  if (campoMunicipio) campoMunicipio.addEventListener("change", function () { consultar(true); });
  consultar(false);
})();
