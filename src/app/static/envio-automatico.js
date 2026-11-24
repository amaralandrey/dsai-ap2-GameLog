// Envia o formulário ao trocar o valor de um campo com `data-envio-automatico` (ex.: status na página do jogo).
// Fica em arquivo estático porque a CSP não aceita script inline nem atributo onchange (INC-10, INC-11).
document.addEventListener("change", function (e) {
  if (e.target.matches("[data-envio-automatico]") && e.target.form) e.target.form.submit();
});
