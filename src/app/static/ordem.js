// Arrastar e soltar os itens de uma lista (LSA-11): monta a ordem completa e envia para POST /listas/<id>/ordem.
// Sem JavaScript, os botões de subir e descer continuam funcionando.
(function () {
  var caixa = document.querySelector(".ordenavel");
  if (!caixa) return;
  var arrastado = null;
  caixa.addEventListener("dragstart", function (e) {
    arrastado = e.target.closest(".item-lista");
    if (arrastado) arrastado.classList.add("arrastando");
  });
  caixa.addEventListener("dragover", function (e) {
    var alvo = e.target.closest(".item-lista");
    if (!arrastado || !alvo || alvo === arrastado) return;
    e.preventDefault();
    var meio = alvo.getBoundingClientRect().top + alvo.offsetHeight / 2;
    caixa.insertBefore(arrastado, e.clientY < meio ? alvo : alvo.nextSibling);
  });
  caixa.addEventListener("dragend", function () {
    if (!arrastado) return;
    arrastado.classList.remove("arrastando");
    arrastado = null;
    var ids = Array.prototype.map.call(caixa.querySelectorAll(".item-lista"), function (el) { return el.dataset.jogo; });
    var corpo = new URLSearchParams({ordem: ids.join(","), csrf_token: caixa.dataset.csrf});
    fetch(caixa.dataset.ordem, {method: "POST", body: corpo, headers: {"X-Requested-With": "fetch"}, credentials: "same-origin"})
      .then(function (r) { if (!r.ok) window.location.reload(); });
  });
})();
